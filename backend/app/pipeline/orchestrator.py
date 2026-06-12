"""Per-vendor state machine + market-level batch runner.

One flow, five stages, run N× concurrent under a semaphore:
    URL -> [INGEST] -> [A: EXTRACT] -> [B: HUNT] -> [C: JUDGE] -> [D: ADVISE]
                                                          -> score -> leaderboard

`gather` is bound by its slowest task; per-stage timeouts (asyncio.wait_for) and
tenacity-style backoff keep the sweep moving — failure is a grey card, never a
raised exception that stalls the whole batch."""

from __future__ import annotations

import asyncio

from app.config import settings
from app.pipeline.advise import advise
from app.pipeline.extract import extract
from app.pipeline.hunt import hunt
from app.pipeline.ingest import ingest
from app.pipeline.judge import judge
from app.schemas import HonestAdStatus, Judgment, MarketResult, VendorResult
from app.scoring import finalize_market, vendor_credibility
from app.telemetry import TelemetryBus, TelemetryEvent


async def run_vendor(
    vendor: str,
    url: str,
    *,
    bus: TelemetryBus,
    naive: bool = False,
) -> VendorResult:
    """Run all five stages for one vendor. Always returns a VendorResult — never
    raises. Per-stage failures are reflected in `status` and grey-carded on UI."""

    # INGEST
    try:
        markdown = await asyncio.wait_for(
            ingest(url, bus=bus, vendor=vendor),
            timeout=settings.SCRAPE_TIMEOUT_S * 2,
        )
    except Exception:
        markdown = ""

    if not markdown.strip():
        result = VendorResult(vendor=vendor, url=url, status="unreachable")
        bus.emit(TelemetryEvent(stage="vendor_done", vendor=vendor))
        return result

    # EXTRACT
    try:
        claims = await asyncio.wait_for(
            extract(markdown, bus=bus, vendor=vendor),
            timeout=settings.LLM_TIMEOUT_S,
        )
    except Exception:
        claims = []

    if not claims:
        result = VendorResult(vendor=vendor, url=url, status="no_claims_extracted")
        bus.emit(TelemetryEvent(stage="vendor_done", vendor=vendor))
        return result

    # HUNT + JUDGE per claim (all concurrent)
    async def _hunt_and_judge(claim) -> Judgment | None:
        try:
            evidence = await asyncio.wait_for(
                hunt(vendor, claim, bus=bus),
                timeout=settings.SCRAPE_TIMEOUT_S * 2,
            )
        except Exception:
            from app.schemas import Evidence
            evidence = Evidence(claim_id=claim.claim_id)
        try:
            return await asyncio.wait_for(
                judge(claim, evidence, bus=bus, naive=naive, vendor=vendor),
                timeout=settings.LLM_TIMEOUT_S * 2,
            )
        except Exception:
            return None

    raw_judgments = await asyncio.gather(
        *[_hunt_and_judge(c) for c in claims], return_exceptions=True
    )
    judgments = [j for j in raw_judgments if isinstance(j, Judgment)]

    # ADVISE
    try:
        advice_text = await asyncio.wait_for(
            advise(vendor, judgments, bus=bus),
            timeout=settings.LLM_TIMEOUT_S,
        )
    except Exception:
        advice_text = ""

    result = VendorResult(
        vendor=vendor,
        url=url,
        status="ok",
        claims=claims,
        judgments=judgments,
        credibility_score=vendor_credibility(judgments),
        advice=advice_text,
    )
    bus.emit(TelemetryEvent(stage="vendor_done", vendor=vendor))
    return result


async def run_market(
    category: str,
    vendor_urls: list[tuple[str, str]],
    *,
    bus: TelemetryBus,
    naive: bool = False,
    n: int | None = None,
    semaphore_size: int | None = None,
) -> MarketResult:
    """Run N vendors concurrently under a semaphore. `naive=True` flips the
    cascade off across every stage that uses it (the race counterfactual)."""
    cap = n or settings.N_VENDORS
    sem_size = semaphore_size or settings.SEMAPHORE
    pairs = list(vendor_urls)[:cap]

    sem = asyncio.Semaphore(sem_size)
    market = MarketResult(category=category)

    async def _bounded(vendor: str, url: str) -> VendorResult:
        async with sem:
            try:
                return await run_vendor(vendor, url, bus=bus, naive=naive)
            except Exception as e:
                # Belt and suspenders: run_vendor itself doesn't raise, but if
                # an upstream library does, we never lose the slot.
                return VendorResult(
                    vendor=vendor,
                    url=url,
                    status="error",
                    advice=f"{type(e).__name__}: {e}"[:240],
                )

    tasks = [asyncio.create_task(_bounded(v, u)) for v, u in pairs]

    for coro in asyncio.as_completed(tasks):
        result = await coro
        market.vendors.append(result)
        # Store partial snapshot on bus so GET /audit/{id}/results is always fresh
        bus.partial_result = market

    finalize_market(market)

    # Honest-ad stage — FEATURE-FLAGGED OFF for the Harness hack (Magnific is
    # not a sponsor here). honest_ad.py + VENDOR_BACKDROP_OVERRIDES stay in tree;
    # flip HONEST_AD_ENABLED=true to re-enable. The import lives inside the
    # branch so honest_ad.py is never even loaded when the flag is off.
    if settings.HONEST_AD_ENABLED:
        from app.pipeline.honest_ad import (
            generate_honest_ad,
            pick_ad_candidates,
            prepare_honest_ad,
        )

        candidates = pick_ad_candidates(list(market.vendors), top_n=settings.HONEST_AD_TOP_N)
        for vendor in candidates:
            if not prepare_honest_ad(vendor):
                continue
            bus.partial_result = market
            bus.emit(
                TelemetryEvent(
                    stage="honest_ad_pending",
                    vendor=vendor.vendor,
                    payload={"n_supported_claims": len(vendor.honest_ad_claims)},
                )
            )
            try:
                url, claims = await asyncio.wait_for(
                    generate_honest_ad(vendor, bus=bus),
                    timeout=125.0,
                )
                vendor.honest_ad_url = url
                vendor.honest_ad_claims = claims
            except Exception:
                # Grey-card the ad; sweep continues.
                vendor.honest_ad_status = HonestAdStatus.IMAGE_UNAVAILABLE
                vendor.honest_ad_error = "Image generation failed"
            finally:
                bus.partial_result = market

    # Snapshot telemetry totals into the result so the dashboard has one source
    # of truth alongside the JSONL replay log.
    from app.scoring import claim_inflation_note
    n_llm = bus.totals.get("n_llm_calls", 0)
    n_esc = bus.totals.get("n_escalated", 0)
    market.telemetry_summary = {
        "run_id": bus.run_id,
        "n_vendors": len(market.vendors),
        "vendor_status_counts": dict(_count_by(v.status for v in market.vendors)),
        "n_claims": sum(len(v.judgments) for v in market.vendors),
        "n_escalated_judgments": sum(
            1 for v in market.vendors for j in v.judgments if j.escalated
        ),
        "market_escalation_rate": round((n_esc / n_llm) if n_llm else 0.0, 4),
        "claim_inflation_note": claim_inflation_note(market.vendors),
        "naive_mode": naive,
        "bus_totals": {**bus.totals, "stage_counts": dict(bus.totals["stage_counts"])},
    }

    bus.partial_result = market
    bus.emit(TelemetryEvent(stage="market_done", vendor=None))
    return market


def _count_by(items):
    from collections import Counter
    return Counter(items)
