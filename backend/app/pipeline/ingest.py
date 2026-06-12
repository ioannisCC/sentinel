"""Stage INGEST. URL -> markdown text. Primary: httpx + trafilatura. Fallback:
Jina Reader (r.jina.ai/ prefix) for JS-heavy pages. Hard fail -> grey card
'unreachable — skipped'. Failure is a STATE, never a propagated exception.

NEVER use Browser Use here. NEVER fetch G2/Capterra directly (they block) —
that's Stage B's snippet-only constraint, mentioned here as cross-ref."""

from __future__ import annotations

import httpx
import trafilatura  # type: ignore[import-untyped]

from app import cache
from app.config import settings
from app.telemetry import TelemetryBus, measure

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def ingest(url: str, *, bus: TelemetryBus, vendor: str | None = None) -> str:
    """Fetch `url`, return clean markdown text. On any failure return an empty
    string and leave the per-vendor status='unreachable' decision to the
    orchestrator (this stage's contract is text-or-empty, not text-or-raise)."""
    async with measure(bus, stage="ingest", vendor=vendor) as _m:
        cached = cache.get("ingest", url)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(
                timeout=settings.SCRAPE_TIMEOUT_S,
                follow_redirects=True,
                headers=_HEADERS,
            ) as http:
                resp = await http.get(url)
                if resp.status_code >= 400:
                    return await _jina_fallback(http, url)
                text = trafilatura.extract(
                    resp.text,
                    include_links=False,
                    include_images=False,
                    favor_recall=True,
                ) or ""
                if len(text.strip()) < 200:
                    text = await _jina_fallback(http, url) or text
                result = text[:15_000]
                if result.strip():
                    cache.set("ingest", url, result)
                return result
        except Exception:
            return ""


async def _jina_fallback(http: httpx.AsyncClient, url: str) -> str:
    """Use Jina Reader as a fallback for JS-heavy or blocked pages."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = await http.get(jina_url, timeout=settings.SCRAPE_TIMEOUT_S * 2)
        if resp.status_code < 400 and resp.text.strip():
            return resp.text[:15_000]
    except Exception:
        pass
    return ""
