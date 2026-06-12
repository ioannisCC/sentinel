"""Stage D · ADVISE. Cheap-tier LLM. Given the vendor's judged claims, produce:
    - 3-5 questions a buyer should ask
    - 1 recommended next step

Output is plain text bound to the VendorResult.advice field."""

from __future__ import annotations

from app.clients import attempt_cost_usd, chat, cost_usd
from app.config import settings
from app.schemas import Judgment, Verdict
from app.telemetry import TelemetryBus, measure

_SYSTEM = """You are a procurement advisor helping enterprise buyers evaluate AI vendor claims.

Given a list of claims and their verification verdicts, write:
1. 3-5 sharp due-diligence questions the buyer should ask this vendor
2. One recommended next step

Be direct and specific. Focus on claims that are unverified or self-reported.
Write in plain text — no markdown headers, no bullet symbols, just numbered questions and a clear next step.
Keep the total response under 200 words.
"""


async def advise(
    vendor: str,
    judgments: list[Judgment],
    *,
    bus: TelemetryBus,
) -> str:
    """Return buyer-facing advice text. Empty string is acceptable on failure."""
    weak = [
        j for j in judgments
        if j.verdict in (Verdict.SELF_REPORTED_ONLY, Verdict.NO_PUBLIC_RECEIPT_FOUND)
    ]
    if not weak:
        return "All claims checked out — standard contract and SLA review recommended."

    claim_lines = "\n".join(
        f"- [{j.verdict.value}] {j.rationale}" for j in weak[:6]
    )
    user_content = (
        f"Vendor: {vendor}\n\n"
        f"Unverified or self-reported claims:\n{claim_lines}\n\n"
        "Write the due-diligence questions and next step."
    )

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_content},
    ]

    async with measure(bus, stage="advise", vendor=vendor) as m:
        m.model = settings.PREMIUM_MODEL if settings.CHEAP_FALLBACK_TO_PREMIUM else settings.CHEAP_MODEL
        try:
            result = await chat("cheap", messages, max_tokens=300, temperature=0.3)
            m.tokens_in = result.tokens_in
            m.tokens_out = result.tokens_out
            m.model = result.model
            m.cost_usd = max(
                cost_usd(result.model, result.tokens_in, result.tokens_out),
                attempt_cost_usd(result.model),
            )
            return result.text.strip()
        except Exception:
            m.cost_usd = attempt_cost_usd(settings.CHEAP_MODEL)
            return ""
