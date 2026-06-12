"""D08 — backfill the 49 historical telemetry_history JSONL files into
ClickHouse. Replays every event through ch_sink synchronously so the demo
queries return real history on first connect.

Usage:
    CLICKHOUSE_URL=http://localhost:8123/default \
    /Users/ioannis/Downloads/sentinel/backend/.venv/bin/python \
        backend/scripts/ch_backfill.py

Idempotent: ClickHouse's MergeTree dedups by (run_id, ts, stage); re-running
just no-ops if rows already exist. Logs per-file counts and a final summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

# So the script can be run from anywhere
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import json
import logging
from datetime import datetime, timezone

from app.ch_sink import sink
from app.schemas import TelemetryEvent


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ch_backfill")


HISTORY_DIR = REPO_ROOT / "backend" / "telemetry_history"


def _coerce_event(row: dict) -> TelemetryEvent | None:
    """Map a historical JSONL row to a TelemetryEvent. Receipts-era rows have
    slightly different shapes; we coerce defensively rather than raise."""
    try:
        ts = row.get("ts")
        if isinstance(ts, str):
            # ISO-with-Z timestamps from Receipts; parse to datetime
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif ts is None:
            ts = datetime.now(timezone.utc)
        return TelemetryEvent(
            stage=row.get("stage", "unknown"),
            ts=ts,
            vendor=row.get("vendor"),
            model=row.get("model"),
            tokens_in=int(row.get("tokens_in") or 0),
            tokens_out=int(row.get("tokens_out") or 0),
            cost_usd=float(row.get("cost_usd") or 0.0),
            latency_ms=float(row.get("latency_ms") or 0.0),
            ttft_ms=row.get("ttft_ms"),
            escalated=bool(row.get("escalated")),
            claim_id=row.get("claim_id"),
            payload=row.get("payload") if isinstance(row.get("payload"), dict) else None,
        )
    except Exception as e:
        log.debug("skipping malformed row: %s", e)
        return None


def main() -> int:
    if not sink.enabled:
        log.error("CLICKHOUSE_URL is blank; set it in .env or env before running.")
        return 1

    files = sorted(HISTORY_DIR.glob("run_*.jsonl"))
    log.info("backfill: %d files in %s", len(files), HISTORY_DIR)

    total_files = 0
    total_rows = 0
    total_skipped = 0

    for f in files:
        run_id = f.stem.removeprefix("run_")
        rows_this_file = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                total_skipped += 1
                continue
            ev = _coerce_event(row)
            if ev is None:
                total_skipped += 1
                continue
            try:
                sink.insert_event_sync(run_id, ev)
                rows_this_file += 1
            except Exception as e:
                log.warning("insert failed for %s: %s", run_id, e)
                total_skipped += 1
        if rows_this_file:
            log.info("  %s → %d rows", run_id, rows_this_file)
            total_files += 1
            total_rows += rows_this_file

    log.info(
        "backfill done: %d files, %d rows, %d skipped",
        total_files, total_rows, total_skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
