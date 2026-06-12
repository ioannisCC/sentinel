"""Stage E · HONEST AD. Magnific Mystic backdrop + structured claim overlay.

The eligibility-floor stage: every other stage uses Akamai-tier LLM inference;
this one uses a second model family (Magnific image gen). That's the "more
than one model" bar the project clears.

What this stage actually does:
    1. Take ONLY the vendor's SUPPORTED judgments (after the receipt-consistency
       guard those are the genuinely-corroborated claims).
    2. Generate ONE clean ad-style background image via Magnific. The image
       has generous negative space and NO text/numbers — claim figures stay
       structured and get overlaid in React.
    3. Cache by sha256(vendor + sorted(supported_claims) + model). Re-runs reuse
       the cached URL. Magnific credits are finite.

Zero supported claims → return (None, []). The card shows a stark honest state."""

from __future__ import annotations

import hashlib
import time
from typing import Optional
from urllib.parse import urlparse

from app.cache import get as cache_get, set as cache_set
from app.clients import MAGNIFIC_CREDIT_ESTIMATE, generate_image
from app.config import settings
from app.schemas import HonestAdStatus, TelemetryEvent, VendorResult, Verdict
from app.telemetry import TelemetryBus


PROMPT_VERSION = "honest-ad-contextual-v2"

_PROMPT_TEMPLATE = """Create one premium editorial ad backdrop for {vendor} ({host}).

Use this company-specific context only to choose mood, subject matter, and composition:
{claim_context}

Company vibe: {vibe}

Composition: widescreen 16:9, polished B2B software campaign image, clean foreground depth, strong negative space in the upper-left and lower-third safe zones for live DOM text overlay. Make it distinct to this company and its substantiated claims; avoid generic stock-photo sameness.

Style: {style}. Photorealistic, magazine quality, refined lighting, confident but restrained. No dark unreadable crops.

CRITICAL: absolutely NO text, NO letters, NO words, NO numbers, NO logos, NO UI screenshots, NO charts, NO typography anywhere in the generated image. The app overlays the exact verified claim text separately."""


MAGNIFIC_MODEL = "realism"
MAGNIFIC_RESOLUTION = "1k"
MAGNIFIC_ASPECT = "widescreen_16_9"


# Vendor backdrop overrides — pre-generated, hand-verified Magnific URLs for
# the demo preset vendors. These take precedence over both the per-run cache
# and live Magnific generation. Rationale:
#   - guarantees Railway/prod renders the *vetted* poster (no Apple-logo
#     regression, no surprise output)
#   - avoids any Magnific credit burn during the demo
#   - works without OAuth/API-key on the prod host
#
# Inclusion rule for the dict:
#   - VERIFIED safe image (no logos, no text, no IP smells)
#   - vendor either has a SUPPORTED claim worth featuring (Forethought,
#     Freshdesk AI), or is a known preset whose card needs *some* vendor-styled
#     backdrop when it scores zero (Decagon, Intercom Fin, Zendesk AI — the
#     overlay text in React makes the "0 with public receipts" line honest)
#
# Deliberately NOT here: Tidio. Even if a run surfaces a SUPPORTED claim, we're
# not featuring it for legal/clarity reasons.
VENDOR_BACKDROP_OVERRIDES: dict[str, str] = {
    # Eligible vendors — verified regenerated posters (no logos)
    "Forethought": "https://pikaso.cdnpk.net/private/production/4561070247/render.jpg?token=exp=1781481600~hmac=84a7c954623a073fc7519e06574df918a8962fe97505ee8562c9f05ce549776d",
    "Freshdesk AI": "https://pikaso.cdnpk.net/private/production/4561070599/render.jpg?token=exp=1781481600~hmac=cf7c789e18f9625f7fdc9c72f1499ad868eedc4ad3de387ebf143542895e0f18",
    # Non-eligible presets — vendor-styled backdrops, "What X markets" overlay
    "Decagon": "https://pikaso.cdnpk.net/private/production/4561493153/render.jpg?token=exp=1781481600~hmac=105eac59503c0417cb07395bed054c5862fec90f5e20e3b899607a5826d9ce6a",
    "Intercom Fin": "https://pikaso.cdnpk.net/private/production/4561493733/render.jpg?token=exp=1781481600~hmac=823441b618940b8a0222ef406c14ba436ec31893772b5b0aabfa54b288045ddf",
    "Zendesk AI": "https://pikaso.cdnpk.net/private/production/4561493979/render.jpg?token=exp=1781481600~hmac=53f418670fb88b9ee63193782e08681941260348431e8d9873bc4f8765db0056",
}


def _supported_claim_texts(vendor: VendorResult) -> list[str]:
    """The exact claim strings the vendor can publicly substantiate. Returned
    as a list of plain strings — React overlays these as DOM text."""
    claim_by_id = {c.claim_id: c for c in vendor.claims}
    out: list[str] = []
    for j in vendor.judgments:
        if j.verdict == Verdict.SUPPORTED and j.claim_id in claim_by_id:
            text = (claim_by_id[j.claim_id].claim or "").strip()
            if text and text not in out:
                out.append(text)
    return out


def _cache_key(
    vendor_name: str,
    supported_claims: list[str],
    model: str,
    prompt: str | None = None,
) -> str:
    if prompt is None:
        return _legacy_cache_key(vendor_name, supported_claims, model)
    payload = "|".join(
        [PROMPT_VERSION, vendor_name, model, prompt or ""] + sorted(supported_claims)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_cache_key(vendor_name: str, supported_claims: list[str], model: str) -> str:
    payload = "|".join([vendor_name, model] + sorted(supported_claims))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _host(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.hostname.replace("www.", "") if parsed.hostname else url
    except Exception:
        return url


def _claim_theme(supported_claims: list[str]) -> str:
    text = " ".join(supported_claims).lower()
    if any(w in text for w in ("resolution", "support", "ticket", "agent", "customer")):
        return "support outcomes"
    if any(w in text for w in ("pipeline", "meeting", "reply", "sales", "sdr")):
        return "revenue outcomes"
    if any(w in text for w in ("cost", "spend", "save", "efficiency", "time")):
        return "operational efficiency"
    if any(w in text for w in ("accuracy", "quality", "deflection", "automation")):
        return "automation quality"
    return "publicly substantiated outcomes"


def _vibe(vendor: VendorResult, supported_claims: list[str]) -> tuple[str, str]:
    haystack = f"{vendor.vendor} {_host(vendor.url)} {' '.join(supported_claims)}".lower()
    if any(w in haystack for w in ("zendesk", "freshdesk", "intercom", "support", "ticket")):
        return (
            "calm customer-support operations, fast handoffs, organized service teams, trust under pressure",
            "warm natural office light, crisp service-desk details, glass and soft fabric textures, human-centered",
        )
    if any(w in haystack for w in ("decagon", "forethought", "agent", "automation", "ai")):
        return (
            "AI operations cockpit, precise automation, enterprise workflows, quiet technical confidence",
            "sleek modern workspace, subtle data-light reflections, premium hardware surfaces, cinematic realism",
        )
    if any(w in haystack for w in ("sales", "sdr", "pipeline", "revenue")):
        return (
            "high-velocity revenue team, focused momentum, measurable pipeline progress",
            "bright commercial photography, motion in shallow depth of field, energetic but not flashy",
        )
    return (
        "credible B2B software, measured proof, executive confidence, no hype",
        "editorial SaaS campaign photography, layered depth, refined neutral palette with one restrained accent",
    )


def _prompt_for(vendor: VendorResult, supported_claims: list[str]) -> str:
    vibe, style = _vibe(vendor, supported_claims)
    claim_context = "\n".join(f"- {claim}" for claim in supported_claims[:4])
    return _PROMPT_TEMPLATE.format(
        vendor=vendor.vendor,
        host=_host(vendor.url),
        claim_context=claim_context,
        vibe=vibe,
        style=style,
    )


def prepare_honest_ad(vendor: VendorResult) -> bool:
    supported = _supported_claim_texts(vendor)
    if not supported:
        # Non-eligible by the SUPPORTED rule. If we have a hardcoded backdrop
        # override for this vendor (demo preset), assign it with an honest
        # status so the card still shows a vendor-styled image — the React
        # overlay will read the lack of substantiated claims correctly.
        override_url = VENDOR_BACKDROP_OVERRIDES.get(vendor.vendor)
        if override_url:
            n_total = len(vendor.judgments)
            vendor.honest_ad_url = override_url
            vendor.honest_ad_claims = []
            vendor.honest_ad_headline = f"What {vendor.vendor} markets"
            vendor.honest_ad_subheadline = (
                f"{n_total} claim{'' if n_total == 1 else 's'} on this page · "
                "0 with an independent public receipt."
            )
            vendor.honest_ad_status = HonestAdStatus.CACHE_HIT
            vendor.honest_ad_error = None
            return False
        vendor.honest_ad_status = HonestAdStatus.NOT_ELIGIBLE
        vendor.honest_ad_error = None
        return False

    theme = _claim_theme(supported)
    vendor.honest_ad_claims = supported
    vendor.honest_ad_headline = f"What {vendor.vendor} can prove publicly"
    vendor.honest_ad_subheadline = (
        f"{len(supported)} receipt-backed {theme} claim"
        f"{'' if len(supported) == 1 else 's'} surfaced in this audit."
    )

    # Override-first for eligible vendors too: if we have a verified poster
    # baked in, use it instead of burning Magnific credits or risking a bad
    # regen on stage. This is what makes Railway/prod actually render the
    # safe, vetted image without OAuth.
    override_url = VENDOR_BACKDROP_OVERRIDES.get(vendor.vendor)
    if override_url:
        vendor.honest_ad_url = override_url
        vendor.honest_ad_status = HonestAdStatus.CACHE_HIT
        vendor.honest_ad_error = None
        return False  # already done — orchestrator's loop will skip live gen

    vendor.honest_ad_prompt = _prompt_for(vendor, supported)
    vendor.honest_ad_status = HonestAdStatus.PENDING
    vendor.honest_ad_error = None
    return True


async def generate_honest_ad(
    vendor: VendorResult, *, bus: TelemetryBus
) -> tuple[Optional[str], list[str]]:
    """Returns (honest_ad_url, supported_claim_texts).
      - (url, [claims])  on success or cache hit
      - (None, [])       if vendor has zero SUPPORTED claims
      - (None, [claims]) if Magnific failed but we still want to show the
        stark honest state with the real claim text"""
    if not prepare_honest_ad(vendor):
        return None, []

    supported = vendor.honest_ad_claims
    prompt = vendor.honest_ad_prompt or _prompt_for(vendor, supported)
    key = _cache_key(vendor.vendor, supported, MAGNIFIC_MODEL, prompt)

    # Cache hit: emit a lightweight telemetry event so the dashboard shows the
    # stage fired, but with zero latency + zero credits — proves cache savings.
    cached = cache_get("honest_ad", key) or cache_get(
        "honest_ad", _legacy_cache_key(vendor.vendor, supported, MAGNIFIC_MODEL)
    )
    if isinstance(cached, dict) and cached.get("url"):
        vendor.honest_ad_url = str(cached["url"])
        vendor.honest_ad_status = HonestAdStatus.CACHE_HIT
        bus.emit(
            TelemetryEvent(
                stage="honest_ad",
                vendor=vendor.vendor,
                latency_ms=0.0,
                payload={
                    "cache": "hit",
                    "model": MAGNIFIC_MODEL,
                    "resolution": MAGNIFIC_RESOLUTION,
                    "credits_estimated": 0,
                    "n_supported_claims": len(supported),
                    "prompt_version": PROMPT_VERSION,
                },
            )
        )
        return vendor.honest_ad_url, supported

    t0 = time.perf_counter()
    url = await generate_image(
        prompt,
        model=MAGNIFIC_MODEL,
        resolution=MAGNIFIC_RESOLUTION,
        aspect_ratio=MAGNIFIC_ASPECT,
        timeout_s=90.0,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0

    credits = MAGNIFIC_CREDIT_ESTIMATE.get(MAGNIFIC_RESOLUTION, 0)
    provider = (
        "mcp_command"
        if settings.HONEST_AD_IMAGE_COMMAND.strip()
        else "api_key"
        if settings.FREEPIK_API_KEY
        else "not_configured"
    )
    bus.emit(
        TelemetryEvent(
            stage="honest_ad",
            vendor=vendor.vendor,
            latency_ms=latency_ms,
            payload={
                "cache": "miss",
                "model": MAGNIFIC_MODEL,
                "resolution": MAGNIFIC_RESOLUTION,
                "aspect_ratio": MAGNIFIC_ASPECT,
                "credits_estimated": credits,
                "n_supported_claims": len(supported),
                "provider": provider,
                "prompt_version": PROMPT_VERSION,
                "ok": url is not None,
            },
        )
    )

    if url:
        vendor.honest_ad_url = url
        vendor.honest_ad_status = HonestAdStatus.GENERATED
        cache_set(
            "honest_ad",
            key,
            {
                "url": url,
                "claims": supported,
                "headline": vendor.honest_ad_headline,
                "subheadline": vendor.honest_ad_subheadline,
                "prompt": prompt,
                "prompt_version": PROMPT_VERSION,
            },
        )
        return url, supported

    # Magnific failed — return the claims anyway so the card can show the stark
    # honest state ("we found these substantiated claims; ad failed to render").
    vendor.honest_ad_status = HonestAdStatus.IMAGE_UNAVAILABLE
    vendor.honest_ad_error = (
        "No image generator configured"
        if provider == "not_configured"
        else "Image generation did not return a URL"
    )
    return None, supported


def pick_ad_candidates(
    vendors: list[VendorResult], *, top_n: int
) -> list[VendorResult]:
    """Pick vendors with the BIGGEST gap between claims-made and supported, plus
    any vendor that has a hardcoded backdrop override (so demo presets always
    render an image even when the audit finds zero SUPPORTED claims this run)."""
    def n_supported(v: VendorResult) -> int:
        return sum(1 for j in v.judgments if j.verdict == Verdict.SUPPORTED)

    eligible = [v for v in vendors if n_supported(v) > 0]
    eligible.sort(key=lambda v: len(v.judgments) - n_supported(v), reverse=True)
    out = eligible if top_n <= 0 else eligible[: max(0, top_n)]

    # Append vendors that have a backdrop override but aren't already in the
    # eligible list. prepare_honest_ad will handle them via the override path.
    seen = {v.vendor for v in out}
    for v in vendors:
        if v.vendor in VENDOR_BACKDROP_OVERRIDES and v.vendor not in seen:
            out.append(v)
    return out
