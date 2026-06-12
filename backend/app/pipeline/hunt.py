"""Stage B · HUNT. Per claim, run 2 Tavily queries:
    "{vendor} case study {metric}"
    "{vendor} reviews results"
and collect snippets + URLs. This stage is TOOLS, not an LLM. Receipts are
FOUND, never inferred.

Never fetch G2 / Capterra directly — they block. Use Tavily snippets only."""

from __future__ import annotations

import asyncio

from app import cache
from app.config import settings
from app.schemas import Claim, Evidence
from app.telemetry import TelemetryBus, measure


async def hunt(
    vendor: str,
    claim: Claim,
    *,
    bus: TelemetryBus,
) -> Evidence:
    """Search the web for substantiation of `claim`. Always returns an Evidence —
    empty snippets/urls is a valid signal (Stage C will turn it into
    SELF_REPORTED_ONLY)."""
    async with measure(bus, stage="hunt", vendor=vendor, claim_id=claim.claim_id) as _m:
        if not settings.TAVILY_API_KEY and not settings.TAVILY_API_KEY_BACKUP:
            return Evidence(claim_id=claim.claim_id)

        cache_key = f"{vendor}:{claim.claim_id}"
        cached = cache.get("hunt", cache_key)
        if cached is not None:
            return Evidence(**cached)

        try:
            result = await _search(vendor, claim)
            cache.set("hunt", cache_key, result.model_dump())
            return result
        except Exception:
            return Evidence(claim_id=claim.claim_id)


async def _search(vendor: str, claim: Claim) -> Evidence:
    from tavily import AsyncTavilyClient  # type: ignore[import-not-found]

    key = settings.TAVILY_API_KEY or settings.TAVILY_API_KEY_BACKUP
    client = AsyncTavilyClient(key)

    queries = [
        f"{vendor} {claim.metric or claim.claim[:50]} case study results",
        f"{vendor} customer review {claim.magnitude or ''} verified",
    ]

    async def _one(query: str) -> list[dict]:
        try:
            resp = await client.search(
                query,
                search_depth="basic",
                max_results=4,
                include_answer=False,
            )
            return resp.get("results", [])
        except Exception:
            return []

    all_results = await asyncio.gather(*[_one(q) for q in queries])

    snippets: list[str] = []
    urls: list[str] = []
    seen: set[str] = set()

    for results in all_results:
        for r in results:
            url = r.get("url", "")
            if url in seen:
                continue
            seen.add(url)
            content = r.get("content", "").strip()
            if content:
                snippets.append(content[:400])
                urls.append(url)

    return Evidence(claim_id=claim.claim_id, snippets=snippets, urls=urls)
