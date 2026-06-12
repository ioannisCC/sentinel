"""Two-tier LLM client.

Boundary: messages-in, text+token-counts-out. We deliberately do NOT unify SDK-native
tool_use here — OpenAI and Anthropic tool schemas diverge enough that abstracting
them at this seam leaks complexity into every caller. Structured output for the
judge is done in the STAGE by prompting for JSON and pydantic-validating the parse.
A parse failure or low confidence on cheap-tier just escalates to premium — the
cascade is the safety net.

Routing (decided at chat() call time by `_use_tf()`):
  TRUEFOUNDRY_BASE_URL + _API_KEY + TF_MODEL_CHEAP + TF_MODEL_PREMIUM set
    → BOTH tiers go through TrueFoundry's OpenAI-compatible gateway.
      Model IDs use TF's "provider-account/model" format from the snippet
      generator (e.g. anthropic-main/claude-sonnet-4-6).
  any of the above blank
    → fall back to the D00 stand-in pair:
        cheap   = CHEAP_BASE_URL + cheap_effective_api_key() + CHEAP_MODEL
                  (defaults to Anthropic OpenAI-compat + haiku-4-5)
        premium = AsyncAnthropic native + PREMIUM_MODEL

D02 will swap the cheap tier to Pioneer — either by registering Pioneer as a
TF custom OpenAI-compatible provider (keeps "all traffic through gateway") or
direct-to-Pioneer for cheap only.

The `generate_image` symbol is preserved (no-op) so honest_ad.py keeps its
import surface; Magnific is feature-flagged OFF for this hack."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.config import cheap_effective_api_key, settings


log = logging.getLogger("sentinel.clients")


Tier = Literal["cheap", "premium"]


def _use_tf() -> bool:
    """All four TF inputs present → route through gateway. One missing → fall
    back. Checked per-call so a mid-run .env reload (uvicorn --reload) routes
    correctly without restarting the workers."""
    return bool(
        settings.TRUEFOUNDRY_BASE_URL
        and settings.TRUEFOUNDRY_API_KEY
        and settings.TF_MODEL_CHEAP
        and settings.TF_MODEL_PREMIUM
    )


def _use_pioneer() -> bool:
    """All three Pioneer inputs present → cheap tier goes direct to Pioneer.
    S1 fallback path: TF not configured, premium stays direct-Anthropic. When
    TF later turns on with Pioneer registered as a custom provider, the TF
    gate wins (checked first) and this becomes the local-dev fallback."""
    return bool(
        settings.PIONEER_BASE_URL
        and settings.PIONEER_API_KEY
        and settings.PIONEER_MODEL
    )


# COST TABLE — verified at platform.claude.com on 2026-06-10.
# Sonnet 4.6: $3 / MTok input, $15 / MTok output (base, no cache, no batch).
# Cheap tier is imputed (per-attempt floor in attempt_cost_usd) so the dashboard
# shows visible infra cost even when the cheap endpoint is on free credits.
#
# TF model IDs are seeded with the same rates as their underlying model so
# cost_usd() doesn't silent-zero on the prefixed strings TF returns. If the
# user fills TF env vars after this module imported, cost_usd() also tries
# a `model.rsplit("/", 1)[-1]` lookup as a safety net.
_PREMIUM_RATES = {"input_per_mtok": 3.0, "output_per_mtok": 15.0}
_CHEAP_RATES = {
    "input_per_mtok": settings.CHEAP_INPUT_PER_MTOK,
    "output_per_mtok": settings.CHEAP_OUTPUT_PER_MTOK,
}
# Pioneer pricing is a documented PLACEHOLDER (PIONEER_INPUT_PER_MTOK /
# PIONEER_OUTPUT_PER_MTOK in config.py default to the same imputed cheap
# rate Receipts used). Surfaces a non-zero per-call cost — the silent-zero
# guard the dispatch flagged. Swap rates in .env once Pioneer publishes them.
_PIONEER_RATES = {
    "input_per_mtok": settings.PIONEER_INPUT_PER_MTOK,
    "output_per_mtok": settings.PIONEER_OUTPUT_PER_MTOK,
}

COST_TABLE: dict[str, dict[str, float]] = {
    settings.CHEAP_MODEL: _CHEAP_RATES,
    settings.PREMIUM_MODEL: _PREMIUM_RATES,
}
if settings.TF_MODEL_CHEAP:
    COST_TABLE[settings.TF_MODEL_CHEAP] = _CHEAP_RATES
if settings.TF_MODEL_PREMIUM:
    COST_TABLE[settings.TF_MODEL_PREMIUM] = _PREMIUM_RATES
if settings.PIONEER_MODEL:
    COST_TABLE[settings.PIONEER_MODEL] = _PIONEER_RATES


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rates = COST_TABLE.get(model)
    # TF returns "provider-account/model" — fall back to the basename so the
    # dashboard doesn't silent-zero just because the TF prefix wasn't seeded.
    if rates is None and "/" in model:
        rates = COST_TABLE.get(model.rsplit("/", 1)[-1])
    if rates is None:
        return 0.0
    return (
        tokens_in / 1_000_000.0 * rates["input_per_mtok"]
        + tokens_out / 1_000_000.0 * rates["output_per_mtok"]
    )


def attempt_cost_usd(model: str) -> float:
    cheap_ids = {settings.CHEAP_MODEL, settings.TF_MODEL_CHEAP, settings.PIONEER_MODEL}
    if model in cheap_ids:
        return settings.CHEAP_ATTEMPT_COST_USD
    if "/" in model and model.rsplit("/", 1)[-1] in cheap_ids:
        return settings.CHEAP_ATTEMPT_COST_USD
    return 0.0


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    tier: Tier
    raw: Any = None
    # Pioneer returns x_pioneer.inference_id on every chat.completions response;
    # the adaptive-feedback POST template is
    # /inferences/{inference_id}/feedback. None when the cheap tier isn't on
    # Pioneer (haiku stand-in, TF gateway, premium tier).
    inference_id: Optional[str] = None


_cheap_client_oai: Optional[AsyncOpenAI] = None
_pioneer_client_oai: Optional[AsyncOpenAI] = None
_premium_client_oai: Optional[AsyncOpenAI] = None
_premium_client_native: Optional[AsyncAnthropic] = None


def _tf_client() -> AsyncOpenAI:
    """Single shared OpenAI-SDK client pointed at the TF gateway. Both tiers
    share it; routing is by model ID."""
    global _cheap_client_oai
    if _cheap_client_oai is None or _cheap_client_oai.base_url != settings.TRUEFOUNDRY_BASE_URL:
        _cheap_client_oai = AsyncOpenAI(
            base_url=settings.TRUEFOUNDRY_BASE_URL,
            api_key=settings.TRUEFOUNDRY_API_KEY,
            max_retries=0,
        )
    return _cheap_client_oai


def _pioneer_client() -> AsyncOpenAI:
    """Pioneer's OpenAI-compatible endpoint. Used by cheap tier on the S1
    fallback path (no TF). Cached separately from _cheap_client_oai so the
    base_url doesn't flap if TF env later turns on."""
    global _pioneer_client_oai
    if _pioneer_client_oai is None or _pioneer_client_oai.base_url != settings.PIONEER_BASE_URL:
        _pioneer_client_oai = AsyncOpenAI(
            base_url=settings.PIONEER_BASE_URL,
            api_key=settings.PIONEER_API_KEY,
            max_retries=0,
        )
    return _pioneer_client_oai


def cheap_client() -> AsyncOpenAI:
    """OpenAI-shaped cheap client. Priority: TF gateway → direct Pioneer →
    D00 Anthropic OpenAI-compat stand-in."""
    if _use_tf():
        return _tf_client()
    if _use_pioneer():
        return _pioneer_client()
    global _cheap_client_oai
    if _cheap_client_oai is None or _cheap_client_oai.base_url != settings.CHEAP_BASE_URL:
        _cheap_client_oai = AsyncOpenAI(
            base_url=settings.CHEAP_BASE_URL,
            api_key=cheap_effective_api_key(),
            max_retries=0,
        )
    return _cheap_client_oai


def premium_client_oai() -> AsyncOpenAI:
    """OpenAI-shaped premium client — only used when TF is on."""
    global _premium_client_oai
    if _premium_client_oai is None or _premium_client_oai.base_url != settings.TRUEFOUNDRY_BASE_URL:
        _premium_client_oai = AsyncOpenAI(
            base_url=settings.TRUEFOUNDRY_BASE_URL,
            api_key=settings.TRUEFOUNDRY_API_KEY,
            max_retries=0,
        )
    return _premium_client_oai


def premium_client_native() -> AsyncAnthropic:
    """Direct-Anthropic fallback. Used only when TF is not configured."""
    global _premium_client_native
    if _premium_client_native is None:
        _premium_client_native = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _premium_client_native


# Back-compat alias — earlier code paths import `premium_client`.
def premium_client() -> AsyncAnthropic:
    return premium_client_native()


def _needs_no_think(model: str) -> bool:
    """`/no_think` disables Qwen3's chain-of-thought scratchpad. Harmless on
    Claude/Haiku (they ignore it) but Pioneer's adaptive endpoint can be
    sensitive to leading system-prompt tokens, so we gate by model. Forward-
    compat: any future Qwen-class CHEAP_MODEL lights this up automatically."""
    return "qwen" in model.lower()


def _inject_no_think(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    injected = False
    for m in messages:
        if m["role"] == "system" and not injected:
            out.append({**m, "content": "/no_think\n" + m["content"]})
            injected = True
        else:
            out.append(m)
    if not injected:
        out.insert(0, {"role": "system", "content": "/no_think"})
    return out


async def _openai_chat(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict[str, str]],
    *,
    tier: Tier,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> ChatResult:
    """Shared OpenAI-SDK call path. Used by cheap (always) and by premium
    when TF is on."""
    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        ),
        timeout=timeout + 0.5,
    )
    msg = resp.choices[0].message if resp.choices else None
    text = (msg.content or "") if msg else ""
    usage = resp.usage
    # OpenAI SDK keeps unknown response fields on `raw_response` / passthrough
    # attrs; Pioneer's `x_pioneer.inference_id` typically lands on the parsed
    # object as `x_pioneer` (an attr) when the SDK is permissive, otherwise in
    # __pydantic_extra__. Walk both safely so we never raise on absence.
    inference_id: Optional[str] = None
    x_pioneer = getattr(resp, "x_pioneer", None)
    if x_pioneer is None:
        extra = getattr(resp, "__pydantic_extra__", None) or {}
        x_pioneer = extra.get("x_pioneer")
    if isinstance(x_pioneer, dict):
        inference_id = x_pioneer.get("inference_id")
    elif x_pioneer is not None:
        inference_id = getattr(x_pioneer, "inference_id", None)
    return ChatResult(
        text=text,
        tokens_in=usage.prompt_tokens if usage else 0,
        tokens_out=usage.completion_tokens if usage else 0,
        model=model,
        tier=tier,
        raw=resp,
        inference_id=inference_id,
    )


async def chat(
    tier: Tier,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.0,
    timeout_s: Optional[float] = None,
) -> ChatResult:
    """Unified messages-in / text+usage-out entrypoint. Both tiers go through here
    so the telemetry wrapper has ONE surface to instrument. Messages use the
    OpenAI shape: [{"role": "system"|"user"|"assistant", "content": "..."}]."""
    if tier == "cheap" and settings.CHEAP_FALLBACK_TO_PREMIUM:
        tier = "premium"

    if timeout_s is not None:
        timeout = timeout_s
    elif tier == "cheap":
        timeout = min(settings.LLM_TIMEOUT_S, settings.CHEAP_LLM_TIMEOUT_S)
    else:
        timeout = settings.LLM_TIMEOUT_S

    use_tf = _use_tf()

    if tier == "cheap":
        if use_tf:
            model = settings.TF_MODEL_CHEAP
        elif _use_pioneer():
            model = settings.PIONEER_MODEL
        else:
            model = settings.CHEAP_MODEL
        msgs = _inject_no_think(messages) if _needs_no_think(model) else messages
        return await _openai_chat(
            cheap_client(),
            model,
            msgs,
            tier="cheap",
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    # premium
    if use_tf:
        return await _openai_chat(
            premium_client_oai(),
            settings.TF_MODEL_PREMIUM,
            messages,
            tier="premium",
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    # Direct-Anthropic native fallback. Split out system message; Anthropic
    # takes it separately.
    model = settings.PREMIUM_MODEL
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    convo = [m for m in messages if m["role"] != "system"]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": convo,
    }
    if system_parts:
        kwargs["system"] = "\n\n".join(system_parts)
    resp = await premium_client_native().messages.create(**kwargs, timeout=timeout)
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    return ChatResult(
        text=text,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        model=model,
        tier="premium",
        raw=resp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pioneer adaptive-inference feedback seam (D02 S4).
# ─────────────────────────────────────────────────────────────────────────────
#
# When the cheap judge disagrees with premium (cheap confidence < threshold →
# premium re-judges), we POST the disagreement pair to Pioneer's adaptive
# feedback endpoint. Fire-and-forget — never blocks the pipeline.
#
# The dispatch's guard rail: "do NOT guess the URL". We expose
# settings.PIONEER_FEEDBACK_URL — blank by default → record_feedback() no-ops
# with a debug log. The rep / docs provide the actual path; set it in .env
# and the next escalation fires a real POST.

async def record_feedback(
    *,
    inference_id: Optional[str],
    cheap_verdict_text: str,
    premium_verdict_text: str,
) -> None:
    """One-shot POST of a cheap/premium disagreement pair to Pioneer's
    per-inference feedback endpoint. Never blocks the pipeline; never raises.

    URL is the documented template `{base}/inferences/{inference_id}/feedback`
    (Pioneer adaptive-inference docs). Body per docs: `{verdict, corrected_output}`.
    Cheap-was-wrong is implicit in the escalation event — we mark verdict
    "incorrect" and ship the premium output as the corrected one.

    The smoke goal (D02 S4): ONE 2xx response logged. Anything beyond that
    (retrain orchestration, batching, retries) is out of scope today."""
    if not inference_id:
        log.debug("record_feedback: skipped (no inference_id — cheap tier wasn't Pioneer)")
        return
    if not settings.PIONEER_API_KEY or not settings.PIONEER_BASE_URL:
        log.debug("record_feedback: skipped (PIONEER_API_KEY or _BASE_URL blank)")
        return

    base = settings.PIONEER_BASE_URL.rstrip("/")
    # Pioneer's feedback path is /inferences/{id}/feedback under the API root,
    # NOT under /v1 — strip a trailing /v1 so the template lands at /inferences.
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/inferences/{inference_id}/feedback"

    payload: dict[str, Any] = {
        "verdict": "incorrect",
        "corrected_output": premium_verdict_text,
    }
    # Light context echo so a future Pioneer dashboard run can correlate the
    # signal back to a specific cheap output — not retrain orchestration.
    if cheap_verdict_text:
        payload["cheap_output"] = cheap_verdict_text

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.PIONEER_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
        log.info(
            "record_feedback: %s %s inference_id=%s body=%s",
            resp.status_code,
            resp.reason_phrase,
            inference_id,
            (resp.text or "")[:200],
        )
    except Exception as e:  # noqa: BLE001 — fire-and-forget by contract
        log.warning("record_feedback failed (inference_id=%s): %s", inference_id, e)


# ─────────────────────────────────────────────────────────────────────────────
# Magnific image generation — FEATURE-FLAGGED OFF for the Harness hack.
# Symbols kept so honest_ad.py imports succeed; the body is a no-op. The flag
# lives at settings.HONEST_AD_ENABLED — orchestrator.py gates the call there.
# ─────────────────────────────────────────────────────────────────────────────

MAGNIFIC_CREDIT_ESTIMATE: dict[str, int] = {"1k": 25, "2k": 50, "4k": 100}


async def generate_image(
    prompt: str,
    *,
    model: str = "realism",
    resolution: str = "1k",
    aspect_ratio: str = "widescreen_16_9",
    timeout_s: float = 60.0,
    poll_interval_s: float = 1.5,
) -> Optional[str]:
    """No-op while HONEST_AD_ENABLED=false. Returns None unconditionally so
    honest_ad.py's IMAGE_UNAVAILABLE grey-card branch is the only outcome if
    the stage is ever invoked with the flag off."""
    return None
