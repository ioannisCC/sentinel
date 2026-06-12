"""Stage C · JUDGE. THE ROUTING STORY.

FrugalGPT cascade + AutoMix-style self-verification:
    1. Cheap tier (Qwen3-8B-FP8) issues verdict + confidence.
    2. If confidence < JUDGE_CONFIDENCE_THRESHOLD, escalate to premium (Sonnet).
    3. Premium verdict wins; `escalated=True` is recorded in the Judgment.

Target escalation rate: ~10-15%, displayed live on the demo.
NAIVE MODE = same code path with cascade disabled, everything Sonnet."""

from __future__ import annotations

import asyncio
import json
import re

from app.clients import attempt_cost_usd, chat, cost_usd, record_feedback
from app.config import settings
from app.schemas import Claim, Evidence, Judgment, Verdict
from app.telemetry import TelemetryBus, measure

_SYSTEM = """You are a claim verification analyst for an AI market auditor.

Given a vendor's claim and web evidence snippets, determine whether the claim is publicly substantiated.

Return ONLY a valid JSON object. No markdown, no explanation, just the JSON.
Required keys:
- "verdict": exactly one of "SUPPORTED", "SELF_REPORTED_ONLY", "NO_PUBLIC_RECEIPT_FOUND"
- "confidence": float 0.0–1.0 (your certainty in this verdict)
- "rationale": 1–2 sentence explanation of your reasoning
- "receipts": list of URLs from the evidence that directly support your verdict (can be [])

Verdict definitions:
- SUPPORTED: at least one independent third-party source (customer case study, analyst report, news article, review site) confirms the claim with data
- SELF_REPORTED_ONLY: the claim exists only on the vendor's own site or press release; no independent confirmation found
- NO_PUBLIC_RECEIPT_FOUND: evidence was searched and either nothing was found or available evidence contradicts the claim

Be rigorous. A vendor quoting themselves on their own blog does not count as independent verification.
"""


def _build_user(claim: Claim, evidence: Evidence) -> str:
    parts = [
        f"Vendor claim: {claim.claim}",
        f"Metric: {claim.metric or 'unspecified'}, Magnitude: {claim.magnitude or 'unspecified'}",
        "",
    ]
    if evidence.snippets:
        parts.append("Web evidence found:")
        for i, (snip, url) in enumerate(zip(evidence.snippets, evidence.urls), 1):
            parts.append(f"[{i}] {url}\n{snip[:300]}")
    else:
        parts.append("No web evidence found for this claim.")
    return "\n".join(parts)


def _strip_json(text: str) -> str:
    text = text.strip()
    # Remove Qwen3 chain-of-thought blocks: <think>...</think>
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


def _parse_judgment(
    text: str,
    claim_id: str,
    escalated: bool,
    evidence_urls: list[str] | None = None,
) -> Judgment | None:
    try:
        data = json.loads(_strip_json(text))
        verdict_str = str(data.get("verdict", "")).upper().strip()
        # Accept partial matches for robustness
        if "SUPPORTED" in verdict_str and "SELF" not in verdict_str:
            verdict = Verdict.SUPPORTED
        elif "SELF" in verdict_str:
            verdict = Verdict.SELF_REPORTED_ONLY
        else:
            verdict = Verdict.NO_PUBLIC_RECEIPT_FOUND

        rationale = str(data.get("rationale", ""))
        receipts = [str(r) for r in (data.get("receipts") or []) if isinstance(r, (str, int))]

        # Filter to URLs that actually appeared in the searched evidence — stops
        # the model from inventing citations.
        if evidence_urls is not None:
            allowed = set(evidence_urls)
            receipts = [r for r in receipts if r in allowed]

        # Enforce verdict/receipts consistency. Both inconsistencies are bad
        # demo material: a NO_PUBLIC_RECEIPT_FOUND judgment carrying a receipt
        # is a self-contradiction on stage; a SUPPORTED judgment with no
        # quotable URL is a hallucination tell.
        if verdict == Verdict.NO_PUBLIC_RECEIPT_FOUND:
            receipts = []
        elif verdict == Verdict.SUPPORTED and not receipts:
            verdict = Verdict.SELF_REPORTED_ONLY
            rationale = (rationale + " [demoted: SUPPORTED claimed without citable receipt]").strip()

        return Judgment(
            claim_id=claim_id,
            verdict=verdict,
            confidence=float(data.get("confidence", 0.5)),
            rationale=rationale,
            receipts=receipts,
            escalated=escalated,
        )
    except Exception:
        return None


async def judge(
    claim: Claim,
    evidence: Evidence,
    *,
    bus: TelemetryBus,
    naive: bool = False,
    vendor: str | None = None,
) -> Judgment:
    """Cascade-judge the claim against found evidence. When `naive=True`, skip
    the cheap tier entirely and route straight to premium (this is the race
    counterfactual — same pipeline, cascade off)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _build_user(claim, evidence)},
    ]
    threshold = settings.JUDGE_CONFIDENCE_THRESHOLD
    cheap_low_conf: Judgment | None = None

    # --- cheap tier first (unless naive mode) ---
    if not naive:
        async with measure(
            bus,
            stage="judge_cheap",
            vendor=vendor,
            claim_id=claim.claim_id,
        ) as m:
            m.model = settings.PREMIUM_MODEL if settings.CHEAP_FALLBACK_TO_PREMIUM else settings.CHEAP_MODEL
            try:
                result = await chat("cheap", messages, max_tokens=512, temperature=0.0)
                m.tokens_in = result.tokens_in
                m.tokens_out = result.tokens_out
                m.model = result.model
                m.cost_usd = max(
                    cost_usd(result.model, result.tokens_in, result.tokens_out),
                    attempt_cost_usd(result.model),
                )

                judgment = _parse_judgment(
                    result.text, claim.claim_id, escalated=False,
                    evidence_urls=evidence.urls,
                )
                if judgment and judgment.confidence >= threshold:
                    return judgment
                if judgment is not None:
                    # Hold onto the under-threshold cheap verdict so the
                    # premium block can ship the disagreement pair to
                    # Pioneer's adaptive feedback endpoint (D02 S4).
                    cheap_low_conf = judgment
            except Exception:
                m.cost_usd = attempt_cost_usd(settings.CHEAP_MODEL)
                pass

    # --- premium escalation ---
    async with measure(
        bus,
        stage="judge_premium",
        vendor=vendor,
        claim_id=claim.claim_id,
    ) as m:
        m.escalated = not naive
        m.model = settings.PREMIUM_MODEL
        try:
            result = await chat("premium", messages, max_tokens=512, temperature=0.0)
            m.tokens_in = result.tokens_in
            m.tokens_out = result.tokens_out
            m.model = result.model
            m.cost_usd = cost_usd(result.model, result.tokens_in, result.tokens_out)

            judgment = _parse_judgment(
                result.text, claim.claim_id, escalated=not naive,
                evidence_urls=evidence.urls,
            )
            if judgment:
                # Fire-and-forget adaptive feedback (D02 S4). No-op when
                # PIONEER_FEEDBACK_URL is blank — see clients.record_feedback.
                if cheap_low_conf is not None:
                    asyncio.create_task(
                        record_feedback(
                            claim=claim.claim,
                            cheap_verdict=cheap_low_conf.model_dump(mode="json"),
                            premium_verdict=judgment.model_dump(mode="json"),
                        )
                    )
                return judgment
        except Exception:
            pass

    # Hard fallback — never let a judge failure kill a vendor. Default to
    # SELF_REPORTED_ONLY (not NO_PUBLIC_RECEIPT_FOUND) per CLAUDE.md: an
    # infrastructure failure is never grounds to imply absence of receipts.
    return Judgment(
        claim_id=claim.claim_id,
        verdict=Verdict.SELF_REPORTED_ONLY,
        confidence=0.0,
        rationale="Judge failed to return a parseable verdict; defaulted to SELF_REPORTED_ONLY.",
        receipts=[],
        escalated=not naive,
    )
