"""FastAPI surface.

POST /audit                       — start a run, returns its run_id (D00)
GET  /audit/{run_id}/stream       — per-run SSE telemetry (D00)
GET  /audit/{run_id}/results      — per-run partial/final MarketResult (D00)
GET  /healthz                     — liveness

D03 additions (sentinel autonomy layer):
GET  /test-vendor/nimbus          — fictional editable vendor page (HTML)
POST /test-vendor/nimbus          — replace claims on the test page (JSON)
GET  /sentinel/status             — live watcher state (watching/triggers/etc.)
GET  /activity/stream             — SSE of the global activity bus
                                    (every sentinel_trigger + per-trigger
                                    pipeline event, ready for D07's feed)

D10 additions (Thesys C1 / OpenUI):
POST /interrogate                 — question + MarketResult → widget spec the
                                    "Interrogate the market" panel renders.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from app import sentinel, test_vendor
from app.telemetry import TelemetryBus
from app.schemas import TelemetryEvent


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the sentinel loop on boot, cancel on shutdown. Lazy state init
    means the watcher's MarketResult and activity bus exist before the first
    request hits the dashboard."""
    await sentinel.start()
    try:
        yield
    finally:
        await sentinel.stop()


app = FastAPI(
    title="Sentinel — Autonomous burden of proof for the agentic web",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# run_id -> bus. In-memory is fine for the demo (single process, no restart).
_RUNS: dict[str, TelemetryBus] = {}
# run_id -> task running the orchestrator. Held so we don't GC the coroutine.
_TASKS: dict[str, asyncio.Task] = {}


class AuditRequest(BaseModel):
    category: str
    vendor_urls: list[tuple[str, str]] = Field(
        ...,
        description="List of (vendor_name, url) tuples to audit.",
    )
    naive: bool = False
    n: Optional[int] = None


class AuditAccepted(BaseModel):
    run_id: str
    stream_url: str
    results_url: str


@app.post("/audit", response_model=AuditAccepted)
async def audit(req: AuditRequest) -> AuditAccepted:
    bus = TelemetryBus()
    _RUNS[bus.run_id] = bus

    from app.pipeline.orchestrator import run_market

    task = asyncio.create_task(
        run_market(
            req.category,
            req.vendor_urls,
            bus=bus,
            naive=req.naive,
            n=req.n,
        )
    )
    _TASKS[bus.run_id] = task

    return AuditAccepted(
        run_id=bus.run_id,
        stream_url=f"/audit/{bus.run_id}/stream",
        results_url=f"/audit/{bus.run_id}/results",
    )


@app.get("/audit/{run_id}/stream")
async def stream(run_id: str) -> EventSourceResponse:
    bus = _RUNS.get(run_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="run not found")

    queue = bus.subscribe()

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {
                    "event": "telemetry",
                    "data": orjson.dumps(event.model_dump(mode="json")).decode("utf-8"),
                }
                if event.stage == "market_done":
                    break
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(gen())


@app.get("/audit/{run_id}/results")
async def results(run_id: str) -> Any:
    """Returns partial or final MarketResult as the audit progresses."""
    bus = _RUNS.get(run_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="run not found")

    task = _TASKS.get(run_id)
    partial = bus.partial_result

    if task and task.done() and not task.exception():
        final = task.result()
        # Keep sentinel state in sync so /interrogate sees live data
        if final and final.vendors:
            sentinel.state().market = final
        return orjson.loads(orjson.dumps(final.model_dump(mode="json")))

    if partial is not None:
        return orjson.loads(orjson.dumps(partial.model_dump(mode="json")))  # type: ignore[attr-defined]

    return {"category": "", "vendors": [], "claim_inflation_index": 0.0, "telemetry_summary": {}}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug/config")
async def debug_config() -> dict:
    from app.clients import _use_pioneer, _use_tf
    from app.config import settings as cfg
    return {
        "use_pioneer": _use_pioneer(),
        "use_tf": _use_tf(),
        "pioneer_base_url": cfg.PIONEER_BASE_URL[:30] + "..." if cfg.PIONEER_BASE_URL else "",
        "pioneer_model": cfg.PIONEER_MODEL,
        "pioneer_api_key_set": bool(cfg.PIONEER_API_KEY),
        "cheap_model": cfg.CHEAP_MODEL,
        "premium_model": cfg.PREMIUM_MODEL,
        "anthropic_key_set": bool(cfg.ANTHROPIC_API_KEY),
        "tavily_key_set": bool(cfg.TAVILY_API_KEY),
    }


# ─────────────────────────────────────────────────────────────────────────────
# D03 — Sentinel autonomy layer
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/test-vendor/nimbus", response_class=HTMLResponse)
async def test_vendor_nimbus_get() -> HTMLResponse:
    """Fictional vendor marketing page. Trafilatura extracts the claim list
    as plain text; the sentinel loop hashes that to detect changes."""
    return HTMLResponse(content=test_vendor.render_html())


class NimbusUpdate(BaseModel):
    headline: Optional[str] = None
    tagline: Optional[str] = None
    claims: Optional[list[str]] = None


@app.post("/test-vendor/nimbus")
async def test_vendor_nimbus_post(payload: NimbusUpdate) -> JSONResponse:
    """Replace any subset of headline/tagline/claims. On stage this is the
    one-line curl that triggers the autonomous re-audit within one interval."""
    s = await test_vendor.update(
        headline=payload.headline,
        tagline=payload.tagline,
        claims=payload.claims,
    )
    return JSONResponse(
        {
            "headline": s.headline,
            "tagline": s.tagline,
            "claims": s.claims,
            "last_modified_ts": s.last_modified_ts,
        }
    )


@app.get("/sentinel/status")
async def sentinel_status() -> dict:
    return sentinel.status_snapshot()


# ─────────────────────────────────────────────────────────────────────────────
# D05 — x402 verdict endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/market/{category}/verdicts")
async def market_verdicts(
    category: str,
    intent: Optional[str] = None,
    request: Request = None,
) -> Any:
    """Paywalled verdict endpoint. Returns HTTP 402 + quote until payment is
    proved via X-Payment header. Passes through in demo mode (no wallet set)."""
    from app.x402 import build_quote, parse_payment_header, verify_payment, demo_hash_for
    from app.config import settings as cfg

    payment_header = request.headers.get("X-Payment") if request else None
    txn_hash = parse_payment_header(payment_header)

    # If wallet is configured, enforce payment
    if cfg.X402_PAY_TO:
        if not txn_hash:
            quote = build_quote(category)
            return JSONResponse(status_code=402, content=quote)
        ok, reason = await verify_payment(txn_hash, category)
        if not ok:
            return JSONResponse(
                status_code=402,
                content={"error": reason, **build_quote(category)},
            )
    else:
        # Demo mode — accept a deterministic hash or no header at all
        if txn_hash:
            from app.x402 import _mark_used
            _mark_used(txn_hash)

    # Fetch live market result from sentinel state
    market = sentinel.state().market
    data = market.model_dump(mode="json")

    # Intent filtering: re-rank vendors by specified categories
    if intent:
        intent_cats = [i.strip() for i in intent.split(",") if i.strip()]
        for vendor in data.get("vendors", []):
            judgments = vendor.get("judgments", [])
            claims = vendor.get("claims", [])
            claim_map = {c["claim_id"]: c for c in claims}
            filtered = [
                j for j in judgments
                if claim_map.get(j["claim_id"], {}).get("claim_type", "") in intent_cats
            ]
            if filtered:
                supported = sum(1 for j in filtered if j["verdict"] == "SUPPORTED")
                vendor["intent_score"] = supported / len(filtered)
            else:
                vendor["intent_score"] = None

    # Emit paid_fetch event so the dashboard "Agents paid" counter ticks
    sentinel.state().activity_bus.emit(
        TelemetryEvent(
            stage="paid_fetch",
            vendor=None,
            payload={"category": category, "txn_hash": txn_hash or demo_hash_for(category)},
        )
    )

    data["paid"] = True
    data["txn_hash"] = txn_hash or demo_hash_for(category)
    data["audit_age_hrs"] = 0  # live data, always fresh from watch loop
    return JSONResponse(content=data)


@app.get("/api/market/{category}/status/{job_id}")
async def market_status(category: str, job_id: str) -> Any:
    """Stub for buyer agent polling. Returns 'complete' for the live loop."""
    return {"status": "complete", "category": category}


class InterrogateRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)


@app.post("/interrogate")
async def interrogate(req: InterrogateRequest) -> EventSourceResponse:
    """D10 — Thesys C1 'Interrogate the market' SSE endpoint.
    Streams a generative-UI response grounded in the live MarketResult."""
    from app.thesys import stream_interrogate

    # Use the freshest available market: sentinel watch-loop state, or the
    # most recently completed /audit run (whichever has more vendors).
    market = sentinel.state().market
    for run_id, task in _TASKS.items():
        if task and task.done() and not task.exception():
            try:
                candidate = task.result()
                if candidate and len(candidate.vendors) > len(market.vendors):
                    market = candidate
                    sentinel.state().market = candidate
            except Exception:
                pass

    async def gen():
        async for chunk in stream_interrogate(req.message, req.history, market):
            yield {"data": chunk}

    return EventSourceResponse(gen())


@app.get("/activity/stream")
async def activity_stream(request: Request) -> EventSourceResponse:
    """Global activity feed — sentinel_trigger, every per-trigger pipeline
    stage event (ingest/extract/hunt/judge_*/advise/vendor_done), and
    sentinel_reaudit_done. D07's UI subscribes here."""
    bus = sentinel.state().activity_bus
    queue = bus.subscribe()

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {
                    "event": "activity",
                    "data": orjson.dumps(event.model_dump(mode="json")).decode("utf-8"),
                }
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(gen())


# ─────────────────────────────────────────────────────────────────────────────
# D10 — Thesys C1 / OpenUI: "Interrogate the market"
# ─────────────────────────────────────────────────────────────────────────────


class InterrogateRequest(BaseModel):
    question: str
    market: dict[str, Any] = Field(
        ...,
        description="A MarketResult (as returned by /audit/{id}/results) to ground the answer.",
    )


@app.post("/interrogate")
async def interrogate_market(req: InterrogateRequest) -> JSONResponse:
    """Question + MarketResult → widget spec. The model is grounded on the
    supplied audit only; the panel renders the widgets in Sentinel's own glass
    components, so generated content never defines the design language."""
    from app.interrogate import interrogate as run_interrogate
    from app.schemas import MarketResult

    if not req.question.strip():
        raise HTTPException(status_code=422, detail="question is required")
    try:
        market = MarketResult.model_validate(req.market)
    except Exception as exc:  # noqa: BLE001 — surface a clean 422 to the panel
        raise HTTPException(status_code=422, detail=f"invalid market payload: {exc}")

    result = await run_interrogate(req.question, market)
    return JSONResponse(result)
