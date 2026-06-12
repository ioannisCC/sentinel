"""Pydantic schemas for every pipeline stage. Schemas-first: no stage emits an
unvalidated dict. The Verdict enum is intentionally narrow — we measure public
substantiation, never truth."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    SELF_REPORTED_ONLY = "SELF_REPORTED_ONLY"
    NO_PUBLIC_RECEIPT_FOUND = "NO_PUBLIC_RECEIPT_FOUND"


class HonestAdStatus(str, Enum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    PENDING = "PENDING"
    CACHE_HIT = "CACHE_HIT"
    GENERATED = "GENERATED"
    IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"


class Claim(BaseModel):
    claim_id: str
    claim: str
    metric: Optional[str] = None
    magnitude: Optional[str] = None
    claim_type: str
    verbatim_span: str


class Evidence(BaseModel):
    claim_id: str
    snippets: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class Judgment(BaseModel):
    claim_id: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    receipts: list[str] = Field(default_factory=list)
    escalated: bool = False


class RedFlag(BaseModel):
    """A language pattern in a claim that historically correlates with
    unsubstantiated marketing copy."""
    claim_id: str
    claim_excerpt: str
    pattern: str
    severity: str  # "high" | "medium" | "low"


class ClusterGroup(BaseModel):
    """A group of similar claims across multiple vendors."""
    metric: str
    count: int
    vendors: list[str]
    supported: int
    self_reported: int
    no_receipt: int


class VendorResult(BaseModel):
    vendor: str
    url: str
    status: str
    claims: list[Claim] = Field(default_factory=list)
    judgments: list[Judgment] = Field(default_factory=list)
    credibility_score: Optional[float] = None
    advice: Optional[str] = None
    red_flags: list[RedFlag] = Field(default_factory=list)
    claim_quality_score: Optional[float] = None
    trend_delta_pct: Optional[int] = None
    trend_new_unsubstantiated: list[str] = Field(default_factory=list)
    # Honest-ad stage: Magnific image URL + the SUPPORTED claim texts that
    # React overlays on top of it as crisp DOM text (never pixels).
    honest_ad_url: Optional[str] = None
    honest_ad_claims: list[str] = Field(default_factory=list)
    honest_ad_headline: Optional[str] = None
    honest_ad_subheadline: Optional[str] = None
    honest_ad_prompt: Optional[str] = None
    honest_ad_status: HonestAdStatus = HonestAdStatus.NOT_ELIGIBLE
    honest_ad_error: Optional[str] = None


class MarketResult(BaseModel):
    category: str
    vendors: list[VendorResult] = Field(default_factory=list)
    claim_inflation_index: float = 0.0
    telemetry_summary: dict[str, Any] = Field(default_factory=dict)
    clusters: list[ClusterGroup] = Field(default_factory=list)
    benchmark: dict[str, Any] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    """One measured external call — or a pipeline lifecycle event when
    stage is 'vendor_complete' or 'market_complete'. In lifecycle events
    `payload` carries the serialised result; all numeric fields are zero."""

    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: str
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    ttft_ms: Optional[float] = None
    cost_usd: float = 0.0
    escalated: bool = False
    vendor: Optional[str] = None
    claim_id: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
