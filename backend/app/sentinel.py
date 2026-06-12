"""Sentinel watch loop — the autonomy layer.

Every WATCH_INTERVAL_S seconds: re-fetch every watched vendor's page (cache-
bypassed), sha256 the extracted text, compare against last seen hash. On
change:
  1. update last hash IMMEDIATELY (debounce — same content doesn't re-fire)
  2. prime the ingest cache with the fresh text so run_vendor's normal
     cached path sees current content (no double-fetch)
  3. emit `sentinel_trigger` on the activity bus
  4. await run_vendor on a per-trigger TelemetryBus parented to the activity
     bus → every pipeline stage (ingest/extract/hunt/judge_cheap/judge_premium/
     advise/vendor_done) mirrors onto activity, ready for D07's feed
  5. swap the new VendorResult into the long-lived MarketResult, finalize it
     (recompute credibility + inflation index + clusters + benchmark)
  6. call publish(market) — seam, no-op without SENSO_API_KEY (D04)
  7. call notify(delta) — seam, no-op without COMPOSIO_API_KEY (D09)
  8. emit `sentinel_reaudit_done` with old→new score

The watch_list seeds with the ai_support_agents preset (is_test=False, never
live-edited on stage) plus Nimbus (is_test=True, our controllable test page).
First observation of any vendor records its hash without triggering — the
real vendors then stay quiet because their content rarely changes; Nimbus
fires when a stage POST arrives at /test-vendor/nimbus.

This module is the AUTONOMY axis on the rubric. The dashboard's status strip
+ activity feed (D07) consume `status()` and the activity-bus SSE stream."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app import cache
from app.config import settings
from app.notify import SentinelDelta, notify
from app.pipeline.ingest import fetch_text_uncached
from app.pipeline.orchestrator import run_vendor
from app.publish import publish
from app.schemas import MarketResult, TelemetryEvent, VendorResult
from app.scoring import finalize_market
from app.telemetry import TelemetryBus


log = logging.getLogger("sentinel.loop")


# Path to the preset that seeds non-test vendors. Built from REPO_ROOT so it
# also works when uvicorn is invoked from any cwd.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vendors"
_DEFAULT_PRESET = "ai_support_agents.json"


@dataclass
class WatchEntry:
    vendor: str
    url: str
    is_test: bool = False
    last_content_hash: Optional[str] = None
    last_audit_at: Optional[float] = None


@dataclass
class SentinelState:
    """Live state held by the watcher. Read by /sentinel/status and the
    activity stream subscribers."""
    activity_bus: TelemetryBus = field(
        default_factory=lambda: TelemetryBus(run_id="sentinel_activity")
    )
    market: MarketResult = field(
        default_factory=lambda: MarketResult(category="AI support agents")
    )
    watch_list: list[WatchEntry] = field(default_factory=list)
    last_check_ts: Optional[float] = None
    triggers_count: int = 0
    task: Optional[asyncio.Task] = None
    _market_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_STATE: Optional[SentinelState] = None


def state() -> SentinelState:
    """Singleton accessor. Lazy so import order doesn't matter."""
    global _STATE
    if _STATE is None:
        _STATE = SentinelState()
        _seed_watch_list(_STATE)
    return _STATE


def _seed_watch_list(s: SentinelState) -> None:
    """Real vendors from the preset + our fictional Nimbus test page. The
    Nimbus URL is configurable so the demo can swap to a Render-hosted copy
    later (constitution §4 forward note) without code change."""
    preset_path = _DATA_DIR / _DEFAULT_PRESET
    try:
        data = json.loads(preset_path.read_text())
        for name, url in data.get("urls", []):
            s.watch_list.append(WatchEntry(vendor=str(name), url=str(url), is_test=False))
    except Exception as e:  # missing preset is a soft fail in tests
        log.warning("sentinel preset load failed: %s", e)

    s.watch_list.append(
        WatchEntry(
            vendor=settings.TEST_VENDOR_NAME,
            url=settings.TEST_VENDOR_URL,
            is_test=True,
        )
    )


def _hash(text: str) -> str:
    """Stable content hash — collapses any whitespace variance so trafilatura's
    minor formatting drift doesn't fire spurious triggers."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _emit_activity(stage: str, *, vendor: Optional[str] = None, payload: Optional[dict] = None) -> None:
    """Push a sentinel-level event onto the activity bus. Pipeline stages
    are pushed through the per-trigger bus's parent_bus forwarding."""
    s = state()
    s.activity_bus.emit(
        TelemetryEvent(stage=stage, vendor=vendor, payload=payload)
    )


async def _process_entry(entry: WatchEntry) -> None:
    """One tick × one vendor. Fetch fresh, hash, diff. On change: re-audit
    and broadcast. Failures never raise — the loop must keep ticking."""
    s = state()
    try:
        text = await fetch_text_uncached(entry.url)
    except Exception as e:
        log.warning("sentinel fetch failed vendor=%s url=%s err=%s", entry.vendor, entry.url, e)
        return

    if not text.strip():
        # Treat unreachable as 'no observable change' — silent. Real vendors
        # going briefly 503 must not flood the activity feed.
        return

    new_hash = _hash(text)

    if entry.last_content_hash is None:
        # First observation. Record baseline; never trigger on first sight.
        entry.last_content_hash = new_hash
        return

    if new_hash == entry.last_content_hash:
        return  # debounce — no change

    # Change detected. Update hash FIRST so a slow re-audit can't re-fire
    # the same content on the next tick.
    old_hash = entry.last_content_hash
    entry.last_content_hash = new_hash

    # Prime the ingest cache with the fresh text so run_vendor's normal
    # cached path reads current content. No double-fetch.
    cache.set("ingest", entry.url, text)

    _emit_activity(
        "sentinel_trigger",
        vendor=entry.vendor,
        payload={
            "url": entry.url,
            "old_hash": old_hash[:12],
            "new_hash": new_hash[:12],
            "is_test": entry.is_test,
        },
    )

    # Capture the old score for the delta.
    old_score: Optional[float] = None
    async with s._market_lock:
        for v in s.market.vendors:
            if v.vendor == entry.vendor:
                old_score = v.credibility_score
                break

    # Per-trigger bus → parent forwards every pipeline event to activity bus.
    run_bus = TelemetryBus(parent_bus=s.activity_bus)
    try:
        new_result: VendorResult = await run_vendor(
            entry.vendor,
            entry.url,
            bus=run_bus,
            naive=False,
        )
    except Exception as e:
        log.exception("sentinel run_vendor failed vendor=%s err=%s", entry.vendor, e)
        return

    new_score = new_result.credibility_score
    entry.last_audit_at = time.time()
    s.triggers_count += 1

    # Splice the new vendor result into the long-lived MarketResult and
    # recompute aggregates. finalize_market is idempotent + in-place.
    async with s._market_lock:
        s.market.vendors = [
            v for v in s.market.vendors if v.vendor != entry.vendor
        ] + [new_result]
        finalize_market(s.market)

    # Flagged seams — these log "skipped: no key" today, light up at D04/D09.
    # We also emit dedicated activity events so D07's feed renders a line
    # for each seam outcome (skipped vs published vs notified).
    try:
        published_url = await publish(s.market)
    except Exception as e:
        log.exception("publish seam raised: %s", e)
        published_url = None
    if published_url:
        publish_status = "ok"
    elif not settings.SENSO_API_KEY:
        publish_status = "skipped:no_key"
    elif not settings.SENSO_GEO_QUESTION_ID:
        publish_status = "skipped:no_geo_question"
    else:
        publish_status = "skipped:idempotent_or_error"
    _emit_activity(
        "sentinel_published",
        vendor=entry.vendor,
        payload={
            "url": published_url,
            "status": publish_status,
        },
    )

    try:
        await notify(SentinelDelta(
            vendor=entry.vendor,
            url=entry.url,
            old_score=old_score,
            new_score=new_score,
            published_url=published_url,
        ))
        notify_status = "ok" if settings.COMPOSIO_API_KEY else "skipped:no_key"
    except Exception as e:
        log.exception("notify seam raised: %s", e)
        notify_status = "error"
    _emit_activity(
        "sentinel_notified",
        vendor=entry.vendor,
        payload={"status": notify_status},
    )

    _emit_activity(
        "sentinel_reaudit_done",
        vendor=entry.vendor,
        payload={
            "old_score": old_score,
            "new_score": new_score,
            "inflation_index": s.market.claim_inflation_index,
            "published_url": published_url,
        },
    )


async def _loop() -> None:
    s = state()
    interval = max(1, int(settings.WATCH_INTERVAL_S))
    log.info(
        "sentinel loop start: watching=%d interval=%ds",
        len(s.watch_list),
        interval,
    )
    try:
        while True:
            s.last_check_ts = time.time()
            # Process entries concurrently — one slow fetch shouldn't delay
            # detection elsewhere. Each _process_entry swallows its own errors.
            await asyncio.gather(
                *[_process_entry(e) for e in s.watch_list],
                return_exceptions=True,
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("sentinel loop cancelled")
        raise


async def start() -> Optional[asyncio.Task]:
    """Called from the FastAPI lifespan. Returns the task so the lifespan
    can cancel it cleanly on shutdown. Respects WATCH_ENABLED."""
    if not settings.WATCH_ENABLED:
        log.info("WATCH_ENABLED=false → sentinel loop not started")
        return None
    s = state()
    if s.task is not None and not s.task.done():
        return s.task
    s.task = asyncio.create_task(_loop(), name="sentinel-loop")
    return s.task


async def stop() -> None:
    s = state()
    if s.task is None:
        return
    s.task.cancel()
    try:
        await s.task
    except (asyncio.CancelledError, Exception):
        pass
    s.task = None


def status_snapshot() -> dict:
    """JSON-safe snapshot for GET /sentinel/status."""
    s = state()
    return {
        "watching": len(s.watch_list),
        "watch_enabled": settings.WATCH_ENABLED,
        "watch_interval_s": settings.WATCH_INTERVAL_S,
        "last_check_ts": s.last_check_ts,
        "triggers_count": s.triggers_count,
        "task_running": bool(s.task and not s.task.done()),
        "vendors": [
            {
                "vendor": e.vendor,
                "url": e.url,
                "is_test": e.is_test,
                "last_audit_at": e.last_audit_at,
                "observed": e.last_content_hash is not None,
            }
            for e in s.watch_list
        ],
        "market": {
            "category": s.market.category,
            "n_vendors_audited": len(s.market.vendors),
            "claim_inflation_index": s.market.claim_inflation_index,
        },
    }
