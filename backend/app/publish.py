"""D04 — publish a finalized MarketResult to Senso / cited.md.

Status (2026-06-12): the publish path is fully wired but parked on a schema
gap. Senso's POST /org/content-engine/publish requires a `geo_question_id`
(UUID) — the GEO/SEO content unit the markdown answers. The D04 dispatch
explicitly forbids running Senso's onboarding flow to mint one, so until a
question id arrives out-of-band (rep / docs.senso.ai dashboard), publish()
no-ops with a structured skip log.

Everything else is built:
- Markdown doc compiler (category + per-vendor verdicts + scores + inflation
  index + evidence links + substantiation-not-truth disclaimer verbatim).
- Stable audit hash (category + sorted vendor fingerprints) so a re-audit
  with no content drift NEVER double-publishes, and a real drift always does.
- Conditional POST to /content-engine/publish targeting the cited.md
  publisher only (afa1052b-… — discovered via GET /org/destinations).
- Idempotency cache: maps audit_hash → content_id in a local JSON file so
  the next publish of the same hash skips; a republish path for changed
  content is wired but not yet exercised.

Return contract: published cited.md URL on success, None on skip or failure.
Never raises — publish is fire-and-forget on the demo path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import REPO_ROOT, settings
from app.schemas import MarketResult, VendorResult, Verdict


log = logging.getLogger("sentinel.publish")


_METHODOLOGY_DISCLAIMER = (
    "Sentinel measures **public substantiation**, never truth. Verdicts are "
    "`SUPPORTED` / `SELF_REPORTED_ONLY` / `NO_PUBLIC_RECEIPT_FOUND`, surfaced "
    "in the UI as 'Publicly substantiated / Self-reported only / No public "
    "receipt'. Absence of a public receipt is not evidence a claim is false — "
    "it is evidence a vendor has not yet placed one on the public web."
)

_VERDICT_LABEL = {
    Verdict.SUPPORTED: "Publicly substantiated",
    Verdict.SELF_REPORTED_ONLY: "Self-reported only",
    Verdict.NO_PUBLIC_RECEIPT_FOUND: "No public receipt",
}

_PUBLISH_CACHE_PATH = REPO_ROOT / "backend" / "app" / "caches" / "publish_idempotency.json"


# ─────────────────────────────────────────────────────────────────────────────
# Doc compilation
# ─────────────────────────────────────────────────────────────────────────────

def _vendor_fingerprint(v: VendorResult) -> str:
    """Stable per-vendor signature → drives the audit hash. Includes the
    score and per-verdict counts so a re-audit that flips even one verdict
    produces a new hash and re-publishes."""
    counts = {verdict.value: 0 for verdict in Verdict}
    for j in v.judgments:
        counts[j.verdict.value] = counts.get(j.verdict.value, 0) + 1
    score = f"{v.credibility_score:.4f}" if v.credibility_score is not None else "none"
    return f"{v.vendor}|{v.url}|{score}|{counts['SUPPORTED']}|{counts['SELF_REPORTED_ONLY']}|{counts['NO_PUBLIC_RECEIPT_FOUND']}"


def audit_hash(market: MarketResult) -> str:
    """Idempotency key. Same input → same hash → publish() short-circuits."""
    fps = sorted(_vendor_fingerprint(v) for v in market.vendors)
    body = f"{market.category}|{market.claim_inflation_index:.4f}|" + "||".join(fps)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _vendor_section(v: VendorResult) -> str:
    pct = f"{round((v.credibility_score or 0) * 100)}%" if v.credibility_score is not None else "—"
    supported = sum(1 for j in v.judgments if j.verdict == Verdict.SUPPORTED)
    self_rep = sum(1 for j in v.judgments if j.verdict == Verdict.SELF_REPORTED_ONLY)
    no_rec = sum(1 for j in v.judgments if j.verdict == Verdict.NO_PUBLIC_RECEIPT_FOUND)

    lines = [
        f"### {v.vendor}",
        "",
        f"- **Publicly substantiated score**: {pct}",
        f"- **Claims surveyed**: {len(v.claims)}",
        f"- **Verdict mix**: {supported} substantiated · {self_rep} self-reported · {no_rec} no public receipt",
        f"- **Marketing page**: <{v.url}>",
        "",
    ]

    if v.judgments:
        lines.append("**Per-claim verdicts:**")
        lines.append("")
        for j in v.judgments:
            claim_text = next((c.claim for c in v.claims if c.claim_id == j.claim_id), j.claim_id)
            label = _VERDICT_LABEL[j.verdict]
            rec = f" · {len(j.receipts)} receipt(s)" if j.receipts else ""
            esc = " · re-checked by premium" if j.escalated else ""
            lines.append(f"- _{label}_ — {claim_text}{rec}{esc}")
            for url in j.receipts[:3]:
                lines.append(f"  - {url}")
        lines.append("")
    return "\n".join(lines)


def _compile_markdown(market: MarketResult) -> str:
    """Structured, citable markdown body for /org/content-engine/publish."""
    n = len(market.vendors)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = [
        f"# {market.category} — Sentinel audit",
        "",
        f"_Autonomous audit · {ts} · {n} vendor{'s' if n != 1 else ''} surveyed_",
        "",
        f"**Market inflation index**: **{market.claim_inflation_index:.2f}×** "
        "(claims made per publicly substantiated claim across this category)",
        "",
        "## Methodology",
        "",
        _METHODOLOGY_DISCLAIMER,
        "",
        "## Vendors (ranked by substantiation density)",
        "",
    ]
    ranked = sorted(market.vendors, key=lambda v: -(v.credibility_score or -1))
    body = "\n".join(_vendor_section(v) for v in ranked)
    footer = [
        "## How to cite",
        "",
        "Agents fetching this audit should cite the canonical cited.md URL "
        "and surface verdict labels verbatim (Publicly substantiated / "
        "Self-reported only / No public receipt). Substantiation reflects "
        "what we found on the public web at audit time, nothing more.",
    ]
    return "\n".join(header) + body + "\n" + "\n".join(footer) + "\n"


def _compile_doc(market: MarketResult) -> dict:
    """Compile MarketResult → engine-publish payload (sans geo_question_id,
    which is injected at POST time from settings)."""
    n = len(market.vendors)
    inflation = market.claim_inflation_index
    seo_title = f"{market.category}: Sentinel audit ({inflation:.2f}× claim inflation)"
    summary = (
        f"Autonomous Sentinel audit of {n} {market.category} vendor"
        f"{'s' if n != 1 else ''}. Market claim-inflation index {inflation:.2f}×. "
        "Ranked by publicly substantiated claim density; we measure public "
        "substantiation, not truth."
    )
    return {
        "seo_title": seo_title,
        "summary": summary,
        "raw_markdown": _compile_markdown(market),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency cache (audit_hash → content_id)
# ─────────────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(_PUBLISH_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    _PUBLISH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PUBLISH_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Publish
# ─────────────────────────────────────────────────────────────────────────────

async def publish(market: MarketResult) -> Optional[str]:
    """Compile the audit, idempotency-check, POST to Senso, return cited.md
    URL. Never raises. Three skip paths the activity feed surfaces:
      - skipped:no_key                   SENSO_API_KEY blank
      - skipped:no_geo_question          SENSO_GEO_QUESTION_ID blank (D04 STOP)
      - skipped:already_published        audit_hash matches last publish

    The last one is the §9 idempotency guarantee: the autonomous loop fires a
    re-audit whenever content hashes drift, but the publish only fires when
    the *audit outcome* actually changed (per-vendor scores, verdicts)."""
    if not settings.SENSO_API_KEY:
        log.warning(
            "publish skipped: no key (category=%s n_vendors=%d)",
            market.category, len(market.vendors),
        )
        return None

    if not settings.SENSO_GEO_QUESTION_ID:
        # Hard STOP per D04 dispatch: don't guess the geo_question_id, don't
        # auto-run the onboarding flow to mint one. Surfaces the gap to the
        # activity feed so the demo room sees "publish: pending geo_question
        # id" rather than silent absence.
        log.warning(
            "publish skipped: SENSO_GEO_QUESTION_ID unset — Senso's publish "
            "schema requires a geo_question_id (category=%s n_vendors=%d)",
            market.category, len(market.vendors),
        )
        return None

    h = audit_hash(market)
    doc = _compile_doc(market)
    cache = _load_cache()
    prior = cache.get(h)
    if prior and prior.get("url"):
        log.info("publish skipped: audit_hash %s already published (%s)", h, prior["url"])
        return prior["url"]

    publisher_ids = [
        pid.strip()
        for pid in settings.SENSO_PUBLISHER_IDS.split(",")
        if pid.strip()
    ]
    payload = {
        "geo_question_id": settings.SENSO_GEO_QUESTION_ID,
        "seo_title": doc["seo_title"],
        "summary": doc["summary"],
        "raw_markdown": doc["raw_markdown"],
        "publisher_ids": publisher_ids or None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    url = settings.SENSO_BASE_URL.rstrip("/") + "/org/content-engine/publish"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers={
                    "X-API-Key": settings.SENSO_API_KEY,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "sentinel/0.1 (cited.md publisher)",
                },
                json=payload,
            )
    except Exception as e:  # noqa: BLE001 — fire-and-forget by contract
        log.warning("publish failed: %s", e)
        return None

    if resp.status_code >= 400:
        log.warning(
            "publish failed: %s %s — body=%s",
            resp.status_code, resp.reason_phrase, (resp.text or "")[:240],
        )
        return None

    try:
        data = resp.json()
    except Exception:
        log.warning("publish: non-JSON response body=%s", (resp.text or "")[:240])
        return None

    # Engine-publish responses include publish_records with per-destination
    # live URLs. Pick the cited.md record if present, else the first live URL.
    published_url = _extract_cited_md_url(data) or _extract_first_url(data)
    content_id = data.get("content_id") or data.get("id")

    # When the publish response doesn't carry the URL yet (Senso commits the
    # publish_record async — `state` flips to "live" within a few seconds),
    # fall back to a follow-up GET /org/content/{content_id} to read the
    # destinations[].external_url that the verification listing populates.
    if not published_url and content_id:
        published_url = await _fetch_external_url(content_id)

    if published_url:
        cache[h] = {
            "url": published_url,
            "content_id": content_id,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_cache(cache)
        log.info("publish ok: %s (audit_hash=%s, content_id=%s)", published_url, h, content_id)
        return published_url

    log.warning(
        "publish: 2xx but no URL in response body keys=%s content_id=%s",
        list(data.keys())[:10], content_id,
    )
    return None


async def _fetch_external_url(content_id: str) -> Optional[str]:
    """Pull the live destinations[].external_url from the verification listing.
    Plain GET /org/content/{id} returns only metadata; the per-destination
    state + URL only show up in /org/content/verification. Senso commits the
    publish_record async, so we retry once with a short backoff."""
    url = settings.SENSO_BASE_URL.rstrip("/") + "/org/content/verification"
    headers = {
        "X-API-Key": settings.SENSO_API_KEY,
        "Accept": "application/json",
        "User-Agent": "sentinel/0.1 (cited.md publisher)",
    }
    params = {"status": "published", "limit": 50}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                log.debug("verification-list %s %s", resp.status_code, (resp.text or "")[:160])
            else:
                data = resp.json()
                for item in data.get("items") or []:
                    if item.get("content_id") != content_id:
                        continue
                    for dest in item.get("destinations") or []:
                        ext = dest.get("external_url")
                        if ext and (dest.get("publisher_slug") == "cited-md" or "cited.md" in ext):
                            if dest.get("state") in {"live", "published"}:
                                return ext
                            log.debug("destination state=%s (waiting)", dest.get("state"))
        except Exception as e:  # noqa: BLE001
            log.debug("verification-list error: %s", e)
        await asyncio.sleep(2.0)
    return None


def _extract_cited_md_url(data: dict) -> Optional[str]:
    # Senso's POST /content-engine/publish returns top-level
    # `publish_destinations` (list of per-destination publish records). Each
    # record carries `external_url` once the destination goes live + a
    # `publisher_slug` we can match to "cited-md".
    candidates: list[dict] = []
    for key in ("publish_destinations", "destinations", "publish_records"):
        v = data.get(key)
        if isinstance(v, list):
            candidates.extend(v)
    pub_dest = data.get("publish_destination")
    if isinstance(pub_dest, dict):
        candidates.append(pub_dest)
    for rec in candidates:
        slug = (
            rec.get("publisher_slug")
            or (rec.get("destination") or {}).get("slug")
            or rec.get("destination_slug")
        )
        url = (
            rec.get("external_url")
            or rec.get("live_url")
            or rec.get("url")
            or rec.get("published_url")
        )
        if url and (slug == "cited-md" or "cited.md" in url):
            return url
    return None


def _extract_first_url(data: dict) -> Optional[str]:
    candidates: list[dict] = []
    for key in ("publish_destinations", "destinations", "publish_records"):
        v = data.get(key)
        if isinstance(v, list):
            candidates.extend(v)
    for rec in candidates:
        url = (
            rec.get("external_url")
            or rec.get("live_url")
            or rec.get("url")
            or rec.get("published_url")
        )
        if url:
            return url
    return data.get("external_url") or data.get("live_url") or data.get("url") or data.get("published_url")
