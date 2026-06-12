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

## What's in here today (D00)

- The Receipts engine, dropped in clean: ingest → extract → hunt → judge →
  advise pipeline; cascade routing (`cheap → premium`); leaderboard + claim
  inflation index; 49 historical telemetry runs preserved under
  `backend/telemetry_history/` for the ClickHouse backfill in D08.
- A new identity: Sentinel wordmark, radar-pulse logo, dark liquid-glass
  surfaces, indigo→cyan accent, desaturated verdict palette.
- A minimal env contract — only `ANTHROPIC_API_KEY` + `TAVILY_API_KEY`
  required at boot. Every later integration (Pioneer, TrueFoundry, Senso,
  x402, ClickHouse, Composio, Thesys) is silently disabled until its
  dispatch wires it up.
- `HONEST_AD_ENABLED=false`. Magnific isn't a sponsor here, so the
  honest-ad stage is flagged off; `honest_ad.py` stays in tree (with the
  vetted `VENDOR_BACKDROP_OVERRIDES`) so flipping the flag re-enables it.

## Quickstart

```sh
# backend
cd backend
uv sync                                # Python 3.12, pinned deps
cp ../.env.example ../.env             # paste ANTHROPIC_API_KEY + TAVILY_API_KEY
uv run uvicorn app.server:app --port 8010

# frontend (separate terminal)
cd frontend
npm install
npm run dev                            # http://localhost:3000, proxies /audit + /healthz → :8010
```

`/healthz` returns `{"status":"ok"}`. Drop `Vendor, https://url` pairs into the
dashboard textarea (see `backend/data/vendors/ai_support_agents.json` for the
preset) and run a sweep — usually 15–60 s for six vendors on the stand-in
cheap tier.

## Build map

| # | Dispatch | Status |
|---|---|---|
| 00 | Scaffold + clean + new identity | ✅ |
| 01 | TrueFoundry gateway routes both tiers | — |
| 02 | Pioneer adaptive cheap tier + feedback loop | — |
| 03 | Sentinel watch loop + controllable test page | — |
| 04 | cited.md publish via Senso | — |
| 05 | x402 paywall on verdict endpoint | — |
| 06 | Buyer agent (the money moment) | — |
| 07 | Activity feed + status strip (the visible loop) | — |
| 08 | ClickHouse sink + 49-run backfill | — |
| 09 | Composio claim-change alerts | — |
| 10 | Thesys C1 "Interrogate the market" panel | — |
| 11 | 3-min demo video + Devpost submission | — |

## Repo layout

```
sentinel/
├─ backend/
│  ├─ app/
│  │  ├─ pipeline/         ingest, extract, hunt, judge, advise, orchestrator,
│  │  │                    red_flag, honest_ad (flagged off)
│  │  ├─ clients.py        cheap (OpenAI-compat) + premium (Anthropic) seam
│  │  ├─ cache.py          sha256-keyed JSON cache (use cache.set(), not raw writes)
│  │  ├─ telemetry.py      TelemetryBus + measure() + JSONL logger
│  │  ├─ scoring.py        credibility score + claim inflation index
│  │  ├─ schemas.py        Pydantic schemas-first contracts
│  │  ├─ config.py         settings + boot-key gate
│  │  └─ server.py         POST /audit · SSE /audit/{id}/stream · GET /healthz
│  ├─ data/vendors/        ai_support_agents.json, ai_sdrs.json
│  └─ telemetry_history/   49 historical run_*.jsonl files for D08 backfill
├─ frontend/
│  └─ src/
│     ├─ App.tsx           leaderboard · VendorCard · SSE machinery
│     ├─ index.css         dark liquid-glass tokens · animation keyframes
│     └─ components/
│        └─ SentinelLogo.tsx   radar-pulse mark
└─ .env.example            every variable the repo knows about, blank
```
