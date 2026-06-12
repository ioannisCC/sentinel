-- D08 — the 3 demo queries the spec called out (§4 ClickHouse sink).
--
-- These run against the `telemetry` table that ch_sink.py creates on first
-- insert. Schema lives in ch_sink._CREATE_TABLE_SQL:
--   telemetry(run_id, ts, stage, vendor, model, tier, tokens_in, tokens_out,
--             cost_usd, latency_ms, ttft_ms, escalated, claim_id, payload)
--
-- All three are demo-ready: copy-paste into the ClickHouse SQL UI (Cloud or
-- local docker UI at :8123/play) and they return the headline numbers the
-- demo strip shows.


-- ─────────────────────────────────────────────────────────────────────────
-- 1.  Escalation rate over runs (Pioneer adaptive-inference story)
-- The cheap judge gets smarter with traffic. Plot this as a line chart
-- across the last N runs and it slopes down as Pioneer's adaptive feedback
-- starts re-training on our escalation signal.
-- ─────────────────────────────────────────────────────────────────────────
SELECT
    run_id,
    min(ts)                                         AS run_started_at,
    countIf(stage = 'judge_cheap')                  AS cheap_attempts,
    countIf(stage = 'judge_premium' AND escalated)  AS escalations,
    round(100.0 * countIf(stage = 'judge_premium' AND escalated)
                / nullIf(countIf(stage = 'judge_cheap'), 0), 1) AS escalation_pct
FROM telemetry
GROUP BY run_id
ORDER BY run_started_at DESC
LIMIT 50;


-- ─────────────────────────────────────────────────────────────────────────
-- 2.  Cost per market over time
-- LLM spend per audit, broken down by tier so the Pioneer-cheap vs Anthropic-
-- premium split is visible at a glance. Demo line: "we spend ~$X per market,
-- 80% of which is the premium tier we escalate to maybe 10% of the time".
-- ─────────────────────────────────────────────────────────────────────────
SELECT
    run_id,
    min(ts)                                         AS run_started_at,
    round(sumIf(cost_usd, tier = 'cheap'),   4)     AS cheap_cost_usd,
    round(sumIf(cost_usd, tier = 'premium'), 4)     AS premium_cost_usd,
    round(sum(cost_usd), 4)                         AS total_cost_usd
FROM telemetry
WHERE stage IN ('judge_cheap', 'judge_premium', 'extract', 'advise')
GROUP BY run_id
ORDER BY run_started_at DESC
LIMIT 50;


-- ─────────────────────────────────────────────────────────────────────────
-- 3.  Top inflated vendors (all-time)
-- The claim-inflation index per vendor across every audit we've ever run.
-- Demo punchline: "marketing inflated for humans is invisible to agents —
-- here are the vendors with the most public claims and the fewest receipts."
-- ─────────────────────────────────────────────────────────────────────────
SELECT
    vendor,
    countIf(stage = 'judge_cheap' OR (stage = 'judge_premium' AND escalated))                    AS claim_attempts,
    countIf((stage = 'judge_premium' AND escalated)
            AND JSONExtractString(payload, 'verdict') = 'SUPPORTED')                              AS substantiated_claims,
    round(claim_attempts / nullIf(substantiated_claims, 0), 2)                                   AS inflation_multiple
FROM telemetry
WHERE vendor IS NOT NULL
GROUP BY vendor
HAVING claim_attempts > 0
ORDER BY claim_attempts DESC, inflation_multiple DESC
LIMIT 25;
