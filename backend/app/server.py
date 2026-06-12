"""FastAPI surface. POST /audit starts a run and returns its run_id. The frontend
opens GET /audit/{run_id}/stream as Server-Sent Events to receive every
TelemetryEvent live — the telemetry IS the demo."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import orjson
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from app.telemetry import TelemetryBus


app = FastAPI(title="Sentinel — Autonomous burden of proof for the agentic web")

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
                    # Send keep-alive comment
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
        return orjson.loads(orjson.dumps(final.model_dump(mode="json")))

    if partial is not None:
        return orjson.loads(orjson.dumps(partial.model_dump(mode="json")))  # type: ignore[attr-defined]

    return {"category": "", "vendors": [], "claim_inflation_index": 0.0, "telemetry_summary": {}}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
