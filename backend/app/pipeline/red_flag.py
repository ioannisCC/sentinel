"""Red Flag Detector. Scans extracted claims for language patterns that
historically correlate with unsubstantiated marketing copy.

Produces a per-vendor RedFlag list and a claim_quality_score in [0, 1]
(1 = pristine, 0 = every claim is flagged)."""

from __future__ import annotations

import re

from app.schemas import Claim, RedFlag


# (compiled_pattern, human_readable_description, severity)
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\bup to\b", re.I),
        "Bounded qualifier ('up to') — actual results are typically far lower",
        "high",
    ),
    (
        re.compile(r"\bas much as\b", re.I),
        "Bounded qualifier ('as much as') — upper bound, not average",
        "high",
    ),
    (
        re.compile(r"\bcustomers?\s+(?:have\s+)?(?:seen|report(?:ed)?|experience[sd]?|achiev(?:ed?|es?)|observ(?:ed?|es?))\b", re.I),
        "Passive voice metric — no named customer, no methodology cited",
        "medium",
    ),
    (
        re.compile(r"\b(?:some|many|most|several|numerous)\s+customers?\b", re.I),
        "Vague denominator — 'some/many customers' without a count",
        "medium",
    ),
    (
        re.compile(r"\b\d{1,3}[x×]\s*(?:faster|cheaper|better|more|improve|boost|increase|reduc)\b", re.I),
        "Round multiplier — verify if independently benchmarked",
        "medium",
    ),
    (
        re.compile(r"\b(?:instantly|seamlessly|effortlessly|automatically|magically)\b", re.I),
        "Weasel word — no measurable meaning",
        "low",
    ),
    (
        re.compile(r"\b(?:industry[- ]leading|best[- ]in[- ]class|world[- ]class|cutting[- ]edge|state[- ]of[- ]the[- ]art)\b", re.I),
        "Superlative without benchmark — no comparison class stated",
        "low",
    ),
    (
        re.compile(r"\b(?:proven|guaranteed|ensures?|always)\b", re.I),
        "Absolute guarantee — extraordinary claim requiring extraordinary evidence",
        "medium",
    ),
]


def detect(claims: list[Claim]) -> list[RedFlag]:
    """Return one RedFlag per (claim, pattern) hit. A single claim can fire
    multiple flags."""
    flags: list[RedFlag] = []
    for claim in claims:
        text = f"{claim.claim} {claim.verbatim_span or ''}"
        for pattern, description, severity in _PATTERNS:
            if pattern.search(text):
                flags.append(
                    RedFlag(
                        claim_id=claim.claim_id,
                        claim_excerpt=claim.claim[:120],
                        pattern=description,
                        severity=severity,
                    )
                )
    return flags


def claim_quality_score(claims: list[Claim], flags: list[RedFlag]) -> float:
    """Fraction of claims that are flag-free, in [0, 1]."""
    if not claims:
        return 1.0
    flagged_ids = {f.claim_id for f in flags}
    clean = sum(1 for c in claims if c.claim_id not in flagged_ids)
    return round(clean / len(claims), 3)
