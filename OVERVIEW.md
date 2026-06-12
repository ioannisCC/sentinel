# SENTINEL — BUILD CONSTITUTION

**Harness Engineering Hack · June 12, 2026 · AWS Builder Loft SF · tokens&**

**SUBMISSION DEADLINE:** 4:30 PM SF (hard). Demos 4:30–5:00. Awards 7:00. Submit on Devpost: 3-minute demo recording + public GitHub repo.

---

## 0. WHAT THIS IS

Sentinel is an autonomous market-claims auditor for the agentic web. It watches the marketing pages of every vendor in a category, detects when claims change, re-audits them against public evidence on a self-improving inference cascade — with no human in the loop — then publishes the structured audit to cited.md where other AI agents can cite it, and charges those agents per fetch via x402. Marketing inflated for humans is invisible to agents; agents buying software need machine-readable trust. Sentinel produces it, keeps it fresh autonomously, and gets paid for it.

**One-liner:** "An autonomous auditor that polices AI marketing claims, publishes the receipts to the agentic web, and gets paid by the agents that cite them."

---

## 1. HONESTY PROTOCOL (non-negotiable)

The claims-auditing ENGINE (pipeline: ingest→extract→hunt→judge→advise, cascade routing, scoring, leaderboard UI) was built by us earlier this week at another event. We DO NOT hide this. If asked: "We built the audit engine earlier this week; today we built the autonomous layer, the new inference stack, the publishing layer, and the payment layer."

- CHECK THE DEVPOST RULES FIRST THING for prior-code policy. If rules require disclosure, disclose in the submission text. The story is a launch trajectory, not a cover-up.
- Everything demoed must be real: real Pioneer inference, real cited.md publish, real x402 402-response flow, real ClickHouse queries. No mocks, no bypasses, no quiet fallbacks.
- **Verdict discipline carries over UNCHANGED:** we measure PUBLIC SUBSTANTIATION, never truth. Enum: `SUPPORTED | SELF_REPORTED_ONLY | NO_PUBLIC_RECEIPT_FOUND`. The word "Unsupported" and labels "Verified/Unverified/No evidence" remain **BANNED** in UI copy.

---

## 2. WHAT'S NEW TODAY vs WHAT'S REUSED

### Reused
*(copy from the receipts repo into the new repo, attribute in README)*

- `backend/app/pipeline/` — `ingest.py`, `extract.py`, `hunt.py`, `judge.py`, `advise.py`, `orchestrator.py`, `red_flag.py` (`honest_ad.py`: copy but **FEATURE-FLAG OFF** — Magnific is not a sponsor here; do not delete, do not call)
- `backend/app/` — `schemas.py`, `scoring.py`, `cache.py`, `telemetry.py`, `config.py`, `clients.py` (heavily modified — see §4), `server.py` (extended)
- `frontend/` — `App.tsx` leaderboard/VendorCard/SSE machinery, `index.css`, ReceiptsLogo → re-skin/rename to Sentinel
- Data presets: `vendors/ai_support_agents.json` etc.

### NEW today (the actual hackathon build — this is what we demo and what we're judged on)

| Feature | Description |
|---|---|
| **Sentinel loop (AUTONOMY)** | Scheduler that re-fetches vendor pages, diffs content hashes, and autonomously triggers re-audits on change. No button. No human. |
| **Pioneer adaptive inference** | Cheap tier moves from Akamai/Qwen-standin to Pioneer's OpenAI-compatible endpoint. Escalation events become labeled feedback → the judge model improves with traffic. This is the technical heart. |
| **TrueFoundry AI Gateway** | ALL LLM traffic (cheap + premium) routes through the gateway. One base-URL change per tier; buys observability + the governance story. |
| **cited.md / Senso publish** | Every completed market audit is compiled and published as structured, citable context on cited.md via Senso. |
| **x402 payment rail** | The audit-fetch API endpoint returns HTTP 402 with a quote; paying agents get the verdict JSON. The "agents pay for trust data" moat. |
| **ClickHouse telemetry warehouse** | Pipe the existing JSONL telemetry (49 historical run files + live runs) into ClickHouse; live analytical queries in the demo. |
| **Composio action layer** | On claim-change detection, Sentinel posts the delta (Slack or GitHub issue or X post) via Composio. The "act on the web" beat. |
| **BUYER AGENT (THE DEMO STAR)** | Standalone autonomous agent that refuses to trust marketing, hits the verdict API, receives the x402 402-quote, PAYS, gets audited verdicts, reasons over them out loud, and picks a vendor on substantiation alone. |
| **LIVE ACTIVITY FEED** | Dashboard feed where every real event animates in as it happens: watching → change detected → re-auditing via Pioneer → published to cited.md → buyer-agent paid $0.01. |
| **Thesys C1 / OpenUI panel** | "Interrogate the market": question in → generative UI (charts/tables grounded in audit JSON) out, via C1 React SDK. Lives inside ONE contained glass card themed to our tokens — generated content renders inside OUR design language, never defines it. **CUT if it clashes visually on first wire-up.** |

---

## 3. SPONSOR / PRIZE MAP (use ≥3; target 5–7)

| Sponsor | What they want | Our integration | Prize |
|---|---|---|---|
| **Pioneer** | Adaptive inference: OpenAI-compatible endpoint, models improve from live traffic + feedback | Cheap tier of judge cascade; escalations logged as corrective feedback | $500 cash + promo Pro ($1500 credits) — GET PROMO CODE FROM REP AT KICKOFF |
| **TrueFoundry** | AI Gateway: route/govern/observe LLM traffic | Both cascade tiers routed through gateway | 1k platform credits |
| **Senso / cited.md** | Publish structured context agents can cite/pay for | Auto-publish every market audit | 2k credits + Context Challenge |
| **ClickHouse** | Fastest analytical DB | Telemetry warehouse + live demo queries | $1600 pool (+$350 Langfuse bonus if we trace via Langfuse→ClickHouse) |
| **Composio** | Agent action/tool execution | Sentinel posts claim-change alerts | $200 |
| **Guild.ai** | "Most Innovative Use of Agents" — likely concept-level (VERIFY AT KICKOFF) | The Sentinel autonomy story itself | **$2,800 — biggest prize** |
| **Render** | Deploy platform | Deploy Sentinel here (instead of Railway) | Render credits |
| **OpenUI/Thesys** | Generative UI standard | Stretch: C1 panel over audit data | $2000 pool |
| ~~Airbyte, Jua~~ | Context store / earth model | **SKIP** — forced fits lose points | — |

**Judging:** Idea 20 · Technical 20 · Tool Use 20 · Presentation 20 · Autonomy 20. The Sentinel loop + x402 + cited.md directly attack Autonomy + Tool Use + Idea.

---

## 4. ARCHITECTURE (target state at 4:30)

```
           ┌─────────────────────────────────────────────────┐
           │ SENTINEL LOOP (new, asyncio task in server)     │
           │  every N min: fetch vendor pages → sha256 diff  │
           │  changed? → enqueue re-audit (autonomous)       │
           └──────────────┬──────────────────────────────────┘
                          ▼
  run_market / run_vendor (REUSED pipeline)
  INGEST → EXTRACT → HUNT(Tavily) → JUDGE → ADVISE
                          │
             JUDGE CASCADE (modified clients.py):
             cheap tier  = Pioneer endpoint  ──┐
             premium     = Claude Sonnet 4.6   ├── BOTH via TrueFoundry Gateway
             escalation event → Pioneer feedback log (adaptive signal)
                          │
                          ▼
  finalize_market → MarketResult
       ├── SSE → React dashboard (reused, re-skinned "Sentinel")
       ├── telemetry → ClickHouse (new sink alongside JSONL)
       ├── publish → Senso → cited.md  (structured citable audit)
       ├── notify → Composio (Slack/GitHub/X post of the delta)
       └── serve → GET /market/{cat}/verdicts  → HTTP 402 x402 quote
                                                 → paid → JSON verdicts
```

### clients.py changes (the only structural surgery)

- `cheap_client()` → base_url = TrueFoundry gateway route → Pioneer model (OpenAI-compatible: verify exact base URL + model name from Pioneer docs at kickoff)
- `premium_client()` → Anthropic Sonnet via TrueFoundry gateway (if Anthropic-passthrough is friction, premium stays direct-Anthropic and ONLY cheap goes through gateway — still a real integration)
- **NEW** `record_feedback(claim, cheap_verdict, premium_verdict)` → POSTs the disagreement pair to Pioneer's feedback API. Every escalation makes the cheap judge better.
- KEEP `cost_usd()` + telemetry hooks. KEEP `CHEAP_FALLBACK_TO_PREMIUM=false` discipline.

### Sentinel loop (`backend/app/sentinel.py` — new)

- `watch_list` table/json: vendor → url → last_content_hash → last_audit_at
- asyncio background task: every `WATCH_INTERVAL_S` (demo: 30s) → fetch → hash → if changed: log `sentinel_trigger` → `run_vendor` → publish + notify
- **DEMO LEVER:** a test vendor page WE control (tiny static page on Render) so we can edit a claim live on stage and watch the loop fire

### cited.md / Senso publish (`backend/app/publish.py` — new)

- After `finalize_market`: compile audit → structured doc: category, per-vendor verdicts, scores, inflation index, evidence links, timestamp, methodology note
- Push via Senso API (docs.senso.ai — get API key at kickoff)
- Store returned cited.md URL on MarketResult → shown in UI ("Published to the agentic web" link)

### x402 paywall (`backend/app/payments.py` — new)

Wrap `GET /api/market/{category}/verdicts`:
- No payment header → 402 + JSON quote (price $0.01 USDC, pay-to address, scheme per x402 spec)
- Valid payment → full verdict JSON
- Demo: terminal curl shows 402 quote → paying client script fetches → 200 with verdicts. If live settlement is shaky on venue wifi, the 402 handshake must still be real code; worst case demo the 402 quote and say settlement is testnet. **Never fake a paid response.**

### ClickHouse sink (`backend/app/ch_sink.py` — new)

- ClickHouse Cloud free trial OR local docker — decide by wifi quality
- Table: `telemetry(run_id, ts, stage, vendor, model, tier, cost_usd, latency_ms, escalated UInt8, payload JSON)`
- Backfill: one-shot script ingesting the 49 historical `run_*.jsonl` files → instant demo depth
- Live: TelemetryBus gets a second sink (async insert, fire-and-forget, **NEVER blocks pipeline**; failure = log and continue)
- 3 canned demo queries: escalation rate by run; cost per market over time; top inflated vendors all-time (+Langfuse tracing if time → bonus $350)

### Composio notify (`backend/app/notify.py` — new)

- One action: post message (Slack channel or GitHub issue — whichever auths fastest at the venue)
- Content: `"⚠ {vendor} changed claims on {url}: {old→new}. Re-audit: score {x}→{y}. Receipts: {cited.md link}"`

### Buyer Agent (`buyer-agent/` — new, standalone)

- **Identity:** A procurement agent tasked: "Choose an AI customer-support vendor. Marketing pages are unverifiable — purchase the audited verdicts and decide on public substantiation only."
- **Flow:** `GET /api/market/{cat}/verdicts` → 402 + quote → pay via x402 → 200 verdict JSON → Claude reasons over verdicts (streamed to terminal, readable pace) → outputs decision (`"Freshdesk AI: 2/2 substantiated. Forethought: 1/3. Recommending Freshdesk."`) → optionally posts decision via Composio
- Backend logs the paid fetch → SSE event → dashboard "agents paid" counter ticks + activity-feed line appears LIVE
- **Idempotent + re-runnable:** must work twice in a row for rehearsal + real demo

### Frontend — DESIGN DIRECTION (non-negotiable aesthetic contract)

Reference: the Triage dashboard. Handcrafted, minimalist, Apple Liquid Glass. **ZERO generic-AI-slop energy.**

**Surface system:**
- Deep near-black base (`#08080f` family)
- Translucent glass panels — `backdrop-filter: blur(14-20px) saturate(150%)`, surface `rgba(18,18,30,~0.55)`, 1px hairline borders `rgba(255,255,255,0.08)`, soft deep shadows
- Port the `dispatch-05 .glass` tokens from the receipts repo — they're proven — then refine, don't rebuild

**Color discipline:**
- Monochrome-first. ONE accent (cool indigo→cyan) used sparingly (active states, live pulse)
- Verdict colors: desaturated emerald/amber/red as the only other hues
- Generous negative space; breathe like a system status page, not a SaaS landing page

**Motion (Framer Motion, light + smooth):**
- Stagger reveals on mount; activity-feed lines slide-in + settle (spring, low stiffness)
- Score changes animate count-up/count-down; slow "breathing" pulse on the WATCHING indicator
- 150–300ms, ease-out. NOTHING bounces hard, nothing loops decoratively, no particles, no 3D
- **Motion is tied to REAL events only** — that's what makes it feel alive vs animated

**Hierarchy of screens:**
1. Sentinel status strip — watching n vendors · last check · autonomous triggers · published links · agents-paid counter
2. **LIVE ACTIVITY FEED** (hero element) — each pipeline event (`sentinel_trigger`, `ingest`, `judge_cheap`, `escalate`, `published`, `paid_fetch`) appears as a minimal glass line with timestamp, animating in from the real SSE stream
3. Leaderboard + vendor cards (ported, re-skinned)
4. Contained C1 "Interrogate the market" glass card (if it survives)

**Verdict labels EXACTLY:** `Publicly substantiated` / `Self-reported only` / `No public receipt`. Banned words stay banned.

**Wordmark:** Sentinel + tagline "Autonomous burden of proof for the agentic web."

---

## 5. REPO & WORKSPACE PLAN

- **NEW public GitHub repo:** `sentinel` — REQUIRED by Devpost submission
- Local: same machine, NEW folder. Copy reusable files in (do not git-clone history — fresh repo, clean commits)
- Attribution line in README: "audit engine adapted from our prior project Receipts (built June 10); everything in §2-NEW built today"
- `.env` NEVER committed. `.env.example` with every var blank.
- Commit rhythm: per dispatch, working state only. No code-health ceremony: demo-hardening only.

---

## 6. ENV VARS / API KEYS (gather at kickoff — sponsor booths/Discord)

```bash
# inference
PIONEER_API_KEY=            # + promo code for Pro plan ($1500 credits) — ASK REP
PIONEER_BASE_URL=           # OpenAI-compatible; from docs.pioneer.ai
PIONEER_MODEL=
TRUEFOUNDRY_API_KEY=        # gateway key — sign up / rep
TRUEFOUNDRY_BASE_URL=       # unified OpenAI-compatible endpoint
ANTHROPIC_API_KEY=          # have it (premium tier)
PREMIUM_MODEL=claude-sonnet-4-6

# evidence
TAVILY_API_KEY=             # have it (+ backup)

# publish / pay / act / store
SENSO_API_KEY=              # docs.senso.ai — booth
X402_PAY_TO=                # our receiving address (reuse prior x402 setup)
X402_PRICE_USD=0.01
COMPOSIO_API_KEY=           # app.composio.dev
THESYS_C1_API_KEY=          # console.thesys.dev — OpenAI-compatible; for the C1 panel
CLICKHOUSE_URL=             # cloud trial or localhost docker
CLICKHOUSE_PASSWORD=

# sentinel
WATCH_INTERVAL_S=30

# flags
HONEST_AD_ENABLED=false     # Magnific OFF — not a sponsor here
```

---

## 7. BUILD ORDER

*Each dispatch independently shippable. **STOP where 3:45 catches us.***

| # | Dispatch | What proves it | Est |
|---|---|---|---|
| **00** | Repo scaffold: copy reusable files, rename, boot backend+frontend, flag off honest_ad, README attribution, port .glass tokens | Local audit runs end-to-end on old stand-in tier | 30m |
| **01** | TrueFoundry gateway: cheap+premium base URLs through gateway | One sweep, telemetry shows gateway models | 30m |
| **02** | Pioneer cheap tier + `record_feedback` on escalation | Sweep on Pioneer; feedback rows visible in Pioneer dashboard | 90m |
| **03** | Sentinel loop + our controllable test vendor page | Edit page → autonomous re-audit fires, no click | 60m |
| **04** | cited.md publish via Senso | Live cited.md URL opens with our audit | 60m |
| **05** | x402 paywall on verdict endpoint | curl 402 quote → paid fetch → JSON | 45m |
| **06** | BUYER AGENT (depends on 05) | Terminal: agent pays, reasons, decides; dashboard counter ticks | 45m |
| **07** | UI: liquid-glass re-skin + LIVE ACTIVITY FEED + status strip | The loop is visible: feed animates from real SSE events | 75m |
| **08** | ClickHouse sink + 49-file backfill + 3 demo queries | Live query in demo | 45m |
| **09** | Composio change-alert | Slack/GitHub post on sentinel trigger | 30m |
| **10** | C1 "Interrogate the market" glass card (`npx skills add thesysdev/openui`) | Question → generated chart inside our theme; CUT if it clashes | 60m |
| **11** | 3-min video recording + Devpost submission | The submission | 45m |

**CUT LINE LOGIC:**
- **00–03** = autonomous + multi-sponsor (the floor)
- **04–06** = the challenge + the buyer-agent money moment (the story)
- **07** = the demo's visual spine — protect it
- **08–10** = prize breadth, in whatever order the afternoon allows
- **11** = MANDATORY. Record the video by **3:45 LATEST** — a submitted imperfect video beats a perfect unsubmitted one.

---

## 8. THE 3-MINUTE DEMO (beats)

| Beat | Time | Content |
|---|---|---|
| **PROBLEM** | 20s | "AI vendors inflate claims 3.4× (we measured). Humans fall for it; agents buying software can't even see it. The agentic web needs machine-readable trust." |
| **PRODUCT** | 25s | The liquid-glass dashboard: a market already audited; verdicts, inflation index, evidence receipts, the status strip showing WATCHING 6 vendors. "Substantiation, not truth" framing in one sentence. |
| **THE AUTONOMOUS LOOP** | 75s | Edit a claim on our test vendor page live → the ACTIVITY FEED lights up line by line: change detected → re-auditing via Pioneer through TrueFoundry → score animates down → published to cited.md (open the live URL) → Composio alert pops. Nobody touched the product. |
| **THE MONEY MOMENT** | 35s | Buyer-agent terminal: needs a vendor, won't trust marketing, hits API → 402 quote → PAYS → receives verdicts → reasons → picks most-substantiated vendor. On the dashboard, "agents paid" ticks up live. "An AI just paid our product for trust data." |
| **DEPTH FLASH** | 20s | ClickHouse query: escalation rate falling across runs = "the cheap judge is learning from its own escalations — Pioneer adaptive inference." (If C1 panel shipped: ask it one question, let the chart generate.) |
| **WHAT'S NEXT** | 15s | Every software category, continuously audited; trust data as a paid primitive of agent commerce; FTC AI-washing tailwind. |

---

## 9. TASTE / DISCIPLINE CARRY-OVERS

- **Build everything, show one thing:** the loop IS the demo; everything else is one flash.
- **Load-bearing, not decorative:** if a sponsor tool can be removed without the demo breaking, either wire it deeper or cut the claim that we "use" it.
- **Principles over state machines; idempotency where retries happen:** sentinel re-audits must not double-publish (hash-key the publishes); buyer-agent must be re-runnable for rehearsal + live without double-charging confusion.
- **Design discipline:** the aesthetic contract in §4 is binding. Handcrafted liquid glass, monochrome-first, motion only on real events. Any generated UI (C1) renders INSIDE our theme or gets cut. If a screen looks like a template, redo or remove it.
- **No defamation surface:** substantiation language everywhere; our controllable test page uses a FICTIONAL vendor name for the live-edit stunt — never live-edit a real company's score on stage theatrically.
- **Dispatch rhythm:** spec → CC executes → report with acceptance checks → next. CC stops on structural surprises.

---

## 10. ADDITIONS & EXTENSIONS

### Staked Dispute Layer (PRIORITY — 45m backend addition)

Let vendors submit counter-evidence to disputed claims with **staked USDC**:
- If their counter-evidence is accepted → stake refunded
- If rejected → stake goes into the evidence bounty pool

This transforms Sentinel from a one-sided auditor (which vendors can ignore) into a two-sided marketplace with adversarial pressure toward truth. It's the Polymarket model applied to marketing claims.

**Endpoint:** accepts a dispute payload + USDC tx hash, queues it for re-judgment, either refunds or redistributes the stake. Creates skin-in-the-game truth-seeking, a sustainable funding mechanism, and a moat — vendors are financially invested in the platform.

### Post-Purchase Agent Reviews

Buyer agents that use vendor software also report back on whether substantiated claims held up in practice. This creates:
- **Pre-purchase audit:** what vendors claim
- **Post-purchase signal:** what agents observed

This is Yelp for the agent economy — but the reviewers are themselves AI agents, which is much harder to fake than human reviews. The moat compounds with usage.

### The "FTC in a Box" Angle

Frame Sentinel as compliance infrastructure: "Generate an audit trail proving your marketing claims before regulators ask." This flips from attacking vendors to serving them.

- Enterprise compliance teams will pay $50k/year for this
- **"Sentinel Certified" badge** — like SOC 2 but for marketing honesty — that vendors embed on their sites
- The badge is verified via a live API call, so it's not gameable

### Demo Imperative

**Make an agent choose, not just fetch.** Show a buyer agent comparing two vendors using Sentinel data and explaining its purchasing decision out loud:

> *"Vendor A claims 99.9% uptime but it's self-reported only; Vendor B's is publicly substantiated — purchasing B."*

That single output is the entire agentic-commerce thesis made concrete in one sentence the judges will remember.
