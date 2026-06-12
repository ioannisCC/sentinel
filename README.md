<p align="center">
  <img src="assets/sentinel-mark.svg" alt="Sentinel" width="128">
</p>

<h1 align="center">Sentinel</h1>

<p align="center"><b>Autonomous burden of proof for the agentic web.</b></p>

Sentinel watches the marketing pages of every vendor in a category, detects
when claims change, re-audits them against public evidence on a self-improving
inference cascade — no human in the loop — then publishes the structured
audit to **cited.md** where other AI agents can cite it, and charges those
agents per fetch via **x402**.

Marketing inflated for humans is invisible to agents. Agents buying software
need machine-readable trust. Sentinel produces it, keeps it fresh
autonomously, and gets paid for it.

> We measure **public substantiation**, never truth. Verdicts are
> `SUPPORTED` / `SELF_REPORTED_ONLY` / `NO_PUBLIC_RECEIPT_FOUND`, surfaced as
> "Publicly substantiated / Self-reported only / No public receipt".

## Attribution

Audit engine adapted from our prior project **Receipts** (built June 10,
2026). Everything in the autonomy / inference / publish / payment layers was
built today at **Harness Engineering Hack** (June 12, 2026 · AWS Builder Loft
SF · tokens&).

## What's in here today

- **Audit engine** (D00, ported from Receipts): ingest → extract → hunt →
  judge → advise pipeline; cascade routing (`cheap → premium`);
  leaderboard + claim inflation index; 49 historical telemetry runs under
  `backend/telemetry_history/` for the ClickHouse backfill in D08.
- **TrueFoundry gateway seam** (D01-prep): both tiers route through TF
  when its four env vars are set; falls back to direct providers otherwise.
- **Pioneer adaptive cheap tier** (D02): `cheap_client()` priority is
  TF → Pioneer → D00 stand-in. `record_feedback()` fires a `{claim,
  cheap_verdict, premium_verdict}` POST to `PIONEER_FEEDBACK_URL` on every
  escalation — fire-and-forget, never blocks. `/no_think` is gated to
  qwen-class models so Pioneer prompts stay clean. Cost surfaces non-zero
  for the Pioneer model string via `PIONEER_INPUT/OUTPUT_PER_MTOK`.
- **Sentinel watch loop** (D03): asyncio task ticks every
  `WATCH_INTERVAL_S`, sha256-diffs fresh-fetched vendor pages, fires
  autonomous re-audits via the existing pipeline. Includes a fictional
  controllable test vendor at `/test-vendor/nimbus` for the live-edit
  stunt. Publish + notify seams (D04/D09) emit dedicated activity events
  even when keys are absent (`skipped:no_key`) so the feed always renders
  the full loop.
- **cited.md publisher** (D04): full markdown compiler (category,
  per-vendor verdicts, scores, inflation index, evidence links,
  substantiation-not-truth disclaimer verbatim). Idempotency cache
  hash-keys audits → no double-publish on unchanged re-audits. Targets
  cited.md's publisher only (`afa1052b-…`). Parked on `geo_question_id`
  schema requirement — Senso onboarding flow deliberately not run.
- **Liquid-glass UI + live activity feed** (D07): status strip subscribes
  `/sentinel/status`; activity feed subscribes `/activity/stream` via SSE
  reusing the audit-stream `EventSource` pattern. Market inflation as the
  hero number; per-vendor inflation on cards. Motion fires only on real
  events (no decorative loops, no rotating taglines). Leaderboard is
  labelled "Most publicly substantiated"; banned vocabulary
  (Unsupported / Verified / Unverified / No evidence) absent.
- **Identity**: Sentinel wordmark, radar-pulse logo, deep near-black glass,
  one indigo→cyan accent, desaturated verdict palette.
- **Env contract**: only `ANTHROPIC_API_KEY` + `TAVILY_API_KEY` required
  at boot. Every later integration (Pioneer, TF, Senso, x402, ClickHouse,
  Composio, Thesys) is silently disabled until its key + the dispatch's
  config var land.
- `HONEST_AD_ENABLED=false`. Magnific isn't a sponsor here.

## Quickstart

```sh
# backend
cd backend
uv sync                                # Python 3.12, pinned deps
cp ../.env.example ../.env             # paste ANTHROPIC_API_KEY + TAVILY_API_KEY
uv run uvicorn app.server:app --host 127.0.0.1 --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev                            # http://localhost:3000
                                       # proxies /audit /healthz /sentinel
                                       # /activity /test-vendor → :8000
```

`/healthz` returns `{"status":"ok"}`. Drop `Vendor, https://url` pairs into the
dashboard textarea (see `backend/data/vendors/ai_support_agents.json` for the
preset) and run a sweep — usually 15–60 s for six vendors on the stand-in
cheap tier. The watch loop boots automatically with `WATCH_ENABLED=true`
(default); POST to `/test-vendor/nimbus` to drive the autonomous re-audit.

## Pricing knobs

Sentinel tracks two different prices:

- **Buyer fetch price**: `X402_PRICE_USD` is the amount charged when another
  agent fetches a published verdict through the future x402 paywall. The
  default is `$0.01` per fetch.
- **LLM spend estimate**: `CHEAP_INPUT_PER_MTOK`, `CHEAP_OUTPUT_PER_MTOK`, and
  `CHEAP_ATTEMPT_COST_USD` drive the dashboard's visible cheap-tier cost. The
  attempt floor keeps infrastructure spend visible even when a sponsor or trial
  endpoint is running on free credits.

Sponsor/vendor prices should be documented in `.env.example` first, then wired
into `backend/app/config.py` if the application needs to read them at runtime.
For tools without a stable public price, the frontend intentionally shows
`pricing not tracked`.

## Airbyte + ClickHouse Cloud

Docker is not required for Sentinel. Set `CLICKHOUSE_URL`,
`CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, and `CLICKHOUSE_DATABASE` to stream
telemetry into ClickHouse Cloud while keeping the JSONL logs as replay backup.
Use Airbyte Cloud to sync external context into the same warehouse. See
`docs/airbyte-clickhouse-cloud.md`.

## Build map

| # | Dispatch | Status |
|---|---|---|
| 00 | Scaffold + clean + new identity | ✅ shipped |
| 01 | TrueFoundry gateway routes both tiers | 🟡 seam in (env-gated; falls back to direct when blank) |
| 02 | Pioneer adaptive cheap tier + feedback loop | 🟡 wired (S1 fallback path) — needs `PIONEER_BASE_URL` + `PIONEER_MODEL` in `.env` and `PIONEER_FEEDBACK_URL` from rep |
| 03 | Sentinel watch loop + controllable test page | ✅ shipped |
| 04 | cited.md publish via Senso | 🟡 compiler + idempotency cache + conditional POST built — parked on `SENSO_GEO_QUESTION_ID` (schema gap; onboarding flow deliberately not run) |
| 05 | x402 paywall on verdict endpoint | ❌ not started (x402 reference clone needed) |
| 06 | Buyer agent (the money moment) | ❌ not started (depends on D05) |
| 07 | Activity feed + status strip (the visible loop) | ✅ shipped |
| 08 | ClickHouse sink + 49-run backfill | ❌ SDK installed; code not yet |
| 09 | Composio claim-change alerts | 🟡 notify seam exists (D03) — Composio wire pending key |
| 10 | Thesys C1 "Interrogate the market" panel | ❌ OpenUI skill installed; integration not yet |
| 11 | 3-min demo video + Devpost submission | ❌ |

Legend: ✅ shipped · 🟡 code wired, parked on a key / external schema · ❌ not started.

## Repo layout

```
sentinel/
├─ backend/
│  ├─ app/
│  │  ├─ pipeline/         ingest, extract, hunt, judge, advise, orchestrator,
│  │  │                    red_flag, honest_ad (flagged off)
│  │  ├─ clients.py        cheap (TF > Pioneer > stand-in) + premium tier seam,
│  │  │                    cost_usd, record_feedback (D02 Pioneer adaptive)
│  │  ├─ cache.py          sha256-keyed JSON cache (use cache.set(), not raw writes)
│  │  ├─ telemetry.py      TelemetryBus + measure() + JSONL logger
│  │  ├─ scoring.py        substantiation score + claim inflation index
│  │  ├─ schemas.py        Pydantic schemas-first contracts
│  │  ├─ config.py         settings + boot-key gate
│  │  ├─ sentinel.py       D03 watch loop: fetch → sha256 diff → re-audit →
│  │  │                    publish/notify seams → activity bus
│  │  ├─ publish.py        D04 cited.md compiler + idempotency cache +
│  │  │                    conditional POST to /content-engine/publish
│  │  ├─ notify.py         D09 Composio delta-post seam
│  │  ├─ test_vendor.py    D03 controllable test page (Nimbus, fictional)
│  │  └─ server.py         POST /audit · SSE /audit/{id}/stream ·
│  │                       GET /sentinel/status · SSE /activity/stream ·
│  │                       GET /healthz · POST /test-vendor/nimbus
│  ├─ data/vendors/        ai_support_agents.json, ai_sdrs.json
│  └─ telemetry_history/   49 historical run_*.jsonl files for D08 backfill
├─ frontend/
│  └─ src/
│     ├─ App.tsx           idle hero + leaderboard + VendorCard
│     ├─ index.css         dark liquid-glass tokens · event-driven keyframes
│     └─ components/
│        ├─ SentinelLogo.tsx   radar-pulse mark
│        ├─ GlassCard.tsx      shared glass primitive (D10 mounts here)
│        ├─ StatusStrip.tsx    /sentinel/status poll · market inflation hero
│        └─ ActivityFeed.tsx   /activity/stream SSE · spring slide-in lines
└─ .env.example            every variable the repo knows about, blank
```
