"""Two-tier LLM client. cheap=OpenAI-compatible endpoint, premium=Anthropic.

Boundary: messages-in, text+token-counts-out. We deliberately do NOT unify SDK-native
tool_use here — OpenAI and Anthropic tool schemas diverge enough that abstracting
them at this seam leaks complexity into every caller. Structured output for the
judge is done in the STAGE by prompting for JSON and pydantic-validating the parse.
A parse failure or low confidence on cheap-tier just escalates to premium — the
cascade is the safety net.

Cheap-tier seam (CHEAP_BASE_URL + CHEAP_API_KEY + CHEAP_MODEL):
  D00 default → Anthropic OpenAI-compat with Haiku 4.5 (the Receipts stand-in)
  D01         → TrueFoundry gateway base URL (model unchanged)
  D02         → Pioneer endpoint + Pioneer model + Pioneer key

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


# COST TABLE — verified at platform.claude.com on 2026-06-10.
# Sonnet 4.6: $3 / MTok input, $15 / MTok output (base, no cache, no batch).
# Cheap tier is imputed (per-attempt floor in attempt_cost_usd) so the dashboard
# shows visible infra cost even when the cheap endpoint is on free credits.
COST_TABLE: dict[str, dict[str, float]] = {
    settings.CHEAP_MODEL: {
        "input_per_mtok": settings.CHEAP_INPUT_PER_MTOK,
        "output_per_mtok": settings.CHEAP_OUTPUT_PER_MTOK,
    },
    settings.PREMIUM_MODEL: {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rates = COST_TABLE.get(model)
    if rates is None:
        return 0.0
    return (
        tokens_in / 1_000_000.0 * rates["input_per_mtok"]
        + tokens_out / 1_000_000.0 * rates["output_per_mtok"]
    )


def attempt_cost_usd(model: str) -> float:
    if model == settings.CHEAP_MODEL:
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


_cheap_client: Optional[AsyncOpenAI] = None
_premium_client: Optional[AsyncAnthropic] = None


def cheap_client() -> AsyncOpenAI:
    global _cheap_client
    if _cheap_client is None:
        _cheap_client = AsyncOpenAI(
            base_url=settings.CHEAP_BASE_URL,
            api_key=cheap_effective_api_key(),
            max_retries=0,
        )
    return _cheap_client


def premium_client() -> AsyncAnthropic:
    global _premium_client
    if _premium_client is None:
        _premium_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _premium_client


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

    if tier == "cheap":
        model = settings.CHEAP_MODEL
        # /no_think prefix was load-bearing for Qwen3 (Receipts' Akamai cheap tier).
        # It's a no-op for Claude/Pioneer and stays harmless; keeping the seam in
        # place means D02 can swap CHEAP_MODEL=Qwen back without code change.
        no_think_messages: list[dict[str, str]] = []
        injected = False
        for m in messages:
            if m["role"] == "system" and not injected:
                no_think_messages.append({**m, "content": "/no_think\n" + m["content"]})
                injected = True
            else:
                no_think_messages.append(m)
        if not injected:
            no_think_messages.insert(0, {"role": "system", "content": "/no_think"})
        resp = await asyncio.wait_for(
            cheap_client().chat.completions.create(
                model=model,
                messages=no_think_messages,  # type: ignore[arg-type]
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
            tier="cheap",
            raw=resp,
        )

    # premium — Anthropic native. Split out system message; Anthropic takes it
    # separately.
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
    resp = await premium_client().messages.create(**kwargs, timeout=timeout)
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
