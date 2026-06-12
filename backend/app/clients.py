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
from dataclasses import dataclass
from typing import Any, Literal, Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.config import cheap_effective_api_key, settings


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

COST_TABLE: dict[str, dict[str, float]] = {
    settings.CHEAP_MODEL: _CHEAP_RATES,
    settings.PREMIUM_MODEL: _PREMIUM_RATES,
}
if settings.TF_MODEL_CHEAP:
    COST_TABLE[settings.TF_MODEL_CHEAP] = _CHEAP_RATES
if settings.TF_MODEL_PREMIUM:
    COST_TABLE[settings.TF_MODEL_PREMIUM] = _PREMIUM_RATES


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
    if model in {settings.CHEAP_MODEL, settings.TF_MODEL_CHEAP}:
        return settings.CHEAP_ATTEMPT_COST_USD
    if "/" in model and model.rsplit("/", 1)[-1] == settings.CHEAP_MODEL:
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


_cheap_client_oai: Optional[AsyncOpenAI] = None
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


def cheap_client() -> AsyncOpenAI:
    """OpenAI-shaped cheap client. TF gateway when configured, else the D00
    Anthropic OpenAI-compat stand-in."""
    if _use_tf():
        return _tf_client()
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


def _inject_no_think(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Receipts-era Qwen3 needed `/no_think` to disable chain-of-thought. It's
    a no-op for Claude/Haiku via TF, harmless to leave in — keeping the seam
    means D02 can swap CHEAP_MODEL=Qwen back without code change."""
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
    return ChatResult(
        text=text,
        tokens_in=usage.prompt_tokens if usage else 0,
        tokens_out=usage.completion_tokens if usage else 0,
        model=model,
        tier=tier,
        raw=resp,
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
        model = settings.TF_MODEL_CHEAP if use_tf else settings.CHEAP_MODEL
        return await _openai_chat(
            cheap_client(),
            model,
            _inject_no_think(messages),
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
