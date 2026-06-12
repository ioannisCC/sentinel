import { useState, useEffect, useRef, useCallback } from 'react'
import { SentinelLogo } from './components/SentinelLogo'
import { StatusStrip } from './components/StatusStrip'
import { ActivityFeed } from './components/ActivityFeed'
import { GlassCard } from './components/GlassCard'
import { InterrogatePanel } from './components/InterrogatePanel'

// Backend API base. Empty string keeps the Vite dev proxy working locally
// (relative /audit hits the Vite proxy → localhost:8000). On Railway the
// frontend is a static build that must call the backend's public URL
// directly, set via VITE_API_URL at build time.
const API_BASE = (((import.meta as unknown as { env?: { VITE_API_URL?: string } }).env?.VITE_API_URL) || '').replace(/\/+$/, '')

// ── Types ────────────────────────────────────────────────────────────────────

interface TelemetryEvent {
  stage: string
  model: string | null
  tokens_in: number
  tokens_out: number
  latency_ms: number
  cost_usd: number
  escalated: boolean
  vendor: string | null
  claim_id: string | null
}

interface Judgment {
  claim_id: string
  verdict: 'SUPPORTED' | 'SELF_REPORTED_ONLY' | 'NO_PUBLIC_RECEIPT_FOUND'
  confidence: number
  rationale: string
  receipts: string[]
  escalated: boolean
}

interface VendorResult {
  vendor: string
  url: string
  status: string
  claims: { claim: string; metric: string | null; magnitude: string | null; claim_id: string }[]
  judgments: Judgment[]
  credibility_score: number | null
  advice: string | null
  honest_ad_url: string | null
  honest_ad_claims: string[]
  honest_ad_headline: string | null
  honest_ad_subheadline: string | null
  honest_ad_prompt: string | null
  honest_ad_status: 'NOT_ELIGIBLE' | 'PENDING' | 'CACHE_HIT' | 'GENERATED' | 'IMAGE_UNAVAILABLE'
  honest_ad_error: string | null
}

interface MarketResult {
  category: string
  vendors: VendorResult[]
  claim_inflation_index: number
  telemetry_summary?: Record<string, unknown>
}

interface AttemptUsage {
  attempts: number
  cost: number
  tokens: number
  noResponse: number
}

interface RunStats {
  totalCost: number
  totalTokens: number
  escalations: number
  totalJudgments: number
  calls: number
  elapsedMs: number
  modelUsage: Record<string, AttemptUsage>
  toolUsage: Record<string, AttemptUsage>
}

// ── Data ─────────────────────────────────────────────────────────────────────

const EXAMPLE_TEXT =
`Intercom Fin, https://www.intercom.com/fin
Decagon, https://decagon.ai
Zendesk AI, https://www.zendesk.com/service/ai
Forethought, https://forethought.ai
Tidio, https://www.tidio.com
Freshdesk AI, https://www.freshworks.com/freshdesk`

const VERDICT_META = {
  SUPPORTED: {
    label: 'Publicly substantiated',
    color: 'var(--verdict-good)',
    bg: 'var(--verdict-good-soft)',
    description: 'Independent public sources (case studies, third-party reviews, published methodology) corroborate this claim. We report public substantiation, not truth — absence of evidence here is not proof a claim is false.',
  },
  SELF_REPORTED_ONLY: {
    label: 'Self-reported only',
    color: 'var(--verdict-warn)',
    bg: 'var(--verdict-warn-soft)',
    description: "The claim appears only on the vendor's own surfaces (site, blog, press releases). No independent public source echoes it — a signal, not a verdict on truth.",
  },
  NO_PUBLIC_RECEIPT_FOUND: {
    label: 'No public receipt',
    color: 'var(--verdict-bad)',
    bg: 'var(--verdict-bad-soft)',
    description: 'We searched the public web and could not find a receipt for this claim. That does not mean the claim is false — we report public substantiation, not truth.',
  },
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function parseVendorText(raw: string): [string, string][] {
  return raw.split('\n').map(l => l.trim()).filter(Boolean).flatMap(line => {
    const sep = line.includes('\t') ? '\t' : ','
    const parts = line.split(sep).map(s => s.trim())
    if (parts.length < 2) return []
    const url = parts[parts.length - 1]
    if (!url.startsWith('http')) return []
    return [[parts[0], url] as [string, string]]
  })
}

function scoreColor(score: number | null) {
  if (score === null) return 'var(--muted)'
  if (score >= 0.7) return 'var(--verdict-good)'
  if (score >= 0.4) return 'var(--verdict-warn)'
  return 'var(--verdict-bad)'
}

function fmtCost(n: number) { return `$${n.toFixed(4)}` }
function fmtMs(ms: number) { return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s` }

function toolLabelForStage(stage: string) {
  if (stage === 'hunt') return 'Tavily search'
  if (stage === 'ingest') return 'Page scrape'
  if (stage === 'honest_ad') return 'Magnific image'
  return null
}

function vendorStatusLabel(v: VendorResult) {
  if (v.credibility_score !== null) return `${Math.round(v.credibility_score * 100)}%`
  if (v.status === 'unreachable') return 'not loaded'
  if (v.status === 'no_claims_extracted') return 'no claims'
  if (v.status === 'error') return 'error'
  return 'checking...'
}

function vendorStatusTitle(v: VendorResult) {
  if (v.credibility_score !== null) return 'Score out of 100 — how many claims have independent public receipts. We measure public substantiation, not truth.'
  if (v.status === 'unreachable') return 'The page could not be loaded or scraped'
  if (v.status === 'no_claims_extracted') return 'The page loaded, but no specific marketing claims were found'
  if (v.status === 'error') return 'This vendor finished with an error'
  return 'This vendor is still being checked'
}

// ── Sub-components ────────────────────────────────────────────────────────────

function hostOf(u: string): string {
  try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u }
}

function vendorInflation(v: VendorResult): string {
  const claims = v.claims.length
  const supported = v.judgments.filter(j => j.verdict === 'SUPPORTED').length
  if (claims === 0) return '—'
  if (supported === 0) return `${claims}/0`
  return `${(claims / supported).toFixed(1)}×`
}

function VendorCard({ v, animIn }: { v: VendorResult; animIn: boolean }) {
  const [adviceOpen, setAdviceOpen] = useState(false)
  const [claimsOpen, setClaimsOpen] = useState(false)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const score = v.credibility_score
  const pct = score !== null ? Math.round(score * 100) : null

  const counts = v.judgments.reduce(
    (acc, j) => { acc[j.verdict] = (acc[j.verdict] || 0) + 1; return acc },
    {} as Record<string, number>
  )

  const judgeMap = Object.fromEntries(v.judgments.map(j => [j.claim_id, j]))

  const uniqueSources = Array.from(new Set(v.judgments.flatMap(j => j.receipts)))
  const inflation = vendorInflation(v)

  return (
    <div className="glass" style={{
      borderRadius: 18,
      padding: '20px 22px',
      transition: 'opacity 0.4s ease, transform 0.4s ease',
      opacity: animIn ? 1 : 0,
      transform: animIn ? 'translateY(0)' : 'translateY(20px)',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14, gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 19, color: 'var(--text)', letterSpacing: '-0.01em', lineHeight: 1.25 }}>
            {v.vendor}
          </div>
          <a href={v.url} target="_blank" rel="noreferrer"
            style={{ color: 'var(--muted)', fontSize: 11, marginTop: 3, display: 'block', textDecoration: 'none', fontFamily: 'var(--font-mono)' }}>
            {v.url.replace(/^https?:\/\//, '')}
          </a>
        </div>
        {pct !== null ? (
          <div
            title={vendorStatusTitle(v)}
            style={{
              color: scoreColor(score), fontFamily: 'var(--font-serif)', fontWeight: 600,
              fontSize: 28, lineHeight: 1, letterSpacing: '-0.02em', cursor: 'help',
            }}>
            {pct}%
          </div>
        ) : (
          <div
            title={vendorStatusTitle(v)}
            style={{
              fontSize: 11,
              color: 'var(--muted)',
              fontStyle: 'italic',
              whiteSpace: 'nowrap',
            }}>
            {vendorStatusLabel(v)}
          </div>
        )}
      </div>

      {/* Score bar */}
      {pct !== null && (
        <div title="Bar shows the substantiation score — how many claims have independent public receipts"
          style={{ height: 2, background: 'rgba(255,255,255,0.08)', borderRadius: 2, marginBottom: 14, overflow: 'hidden', cursor: 'help' }}>
          <div style={{ height: '100%', width: `${pct}%`, background: scoreColor(score), transition: 'width 0.8s ease', opacity: 0.7 }} />
        </div>
      )}

      {/* Status message for failed pages */}
      {v.status !== 'ok' && (
        <div style={{ color: 'var(--muted)', fontSize: 12, fontStyle: 'italic', marginBottom: 8 }}>
          {v.status === 'unreachable'
            ? 'Could not load this vendor page'
            : 'No specific claims found on this page'}
        </div>
      )}

      {/* Per-vendor inflation row */}
      {v.claims.length > 0 && (
        <div
          title="Per-vendor inflation: claims made on this page per publicly substantiated claim. Higher = more self-reported copy."
          style={{
            display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12,
            fontSize: 11, color: 'var(--muted)', cursor: 'help',
          }}>
          <span style={{ letterSpacing: '0.10em', textTransform: 'uppercase', fontWeight: 600 }}>
            Inflation
          </span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontWeight: 600,
            color: inflation === '—' ? 'var(--muted)' : 'var(--verdict-warn)',
            fontSize: 13, fontVariantNumeric: 'tabular-nums',
          }}>
            {inflation}
          </span>
          <span style={{ fontSize: 10 }}>
            {v.claims.length} claim{v.claims.length === 1 ? '' : 's'} · {v.judgments.filter(j => j.verdict === 'SUPPORTED').length} substantiated
          </span>
        </div>
      )}

      {/* Verdict badges — neutral pills */}
      {v.judgments.length > 0 && (
        <div style={{ marginBottom: 14, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {Object.entries(counts).map(([verdict, count]) => {
            const meta = VERDICT_META[verdict as keyof typeof VERDICT_META]
            return (
              <span key={verdict} title={meta.description} style={{
                background: meta.bg,
                color: meta.color,
                borderRadius: 9999,
                padding: '3px 11px',
                fontSize: 11,
                fontWeight: 500,
                letterSpacing: '-0.005em',
                cursor: 'help',
              }}>
                <strong style={{ fontWeight: 600 }}>{count}</strong> {meta.label.toLowerCase()}
              </span>
            )
          })}
        </div>
      )}

      {/* Expand: individual claims */}
      {v.claims.length > 0 && (
        <>
          <button onClick={() => setClaimsOpen(x => !x)} className="pill" style={{ height: 30, fontSize: 12, padding: '0 14px', marginRight: 6 }}>
            {claimsOpen ? 'Hide claims' : `${v.claims.length} claim${v.claims.length === 1 ? '' : 's'}`}
          </button>
          {claimsOpen && (
            <div style={{ marginTop: 12 }}>
              {v.claims.map((c) => {
                const j = judgeMap[c.claim_id]
                if (!j) return null
                const meta = VERDICT_META[j.verdict]
                return (
                  <div key={c.claim_id} style={{
                    padding: '12px 14px', marginBottom: 8,
                    background: meta.bg, borderRadius: 12,
                    border: '1px solid rgba(15,23,42,0.05)',
                  }}>
                    <div style={{ fontSize: 13, color: 'var(--text)', marginBottom: 6, fontFamily: 'var(--font-serif)', fontWeight: 500, lineHeight: 1.4 }}>
                      {c.claim}
                    </div>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 }}>
                      <span style={{ fontSize: 10, color: meta.color, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {meta.label}
                      </span>
                      <span style={{ fontSize: 10, color: 'var(--muted)' }}>{Math.round(j.confidence * 100)}% confidence</span>
                      {j.escalated && (
                        <span
                          title="Cheap-tier model was uncertain — frontier model re-checked this claim"
                          style={{ fontSize: 10, color: 'var(--accent)', cursor: 'help', fontWeight: 500 }}>
                          re-checked
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-2)', lineHeight: 1.5 }}>{j.rationale}</div>
                    {j.receipts.length > 0 && (
                      <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                        <span style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Receipts</span>
                        {j.receipts.map((url, i) => (
                          <a key={i} href={url} target="_blank" rel="noreferrer" title={url} style={{
                            fontSize: 10, color: 'var(--accent)',
                            background: 'rgba(99,102,241,0.10)',
                            padding: '2px 8px', borderRadius: 9999,
                            textDecoration: 'none', fontFamily: 'var(--font-mono)',
                            maxWidth: 220, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                          }}>{hostOf(url)} ↗</a>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* Web sources — always visible toggle, lists every URL the judge cited */}
      {uniqueSources.length > 0 && (
        <>
          <button onClick={() => setSourcesOpen(x => !x)} className="pill" style={{ height: 30, fontSize: 12, padding: '0 14px', marginRight: 6, marginTop: 8 }}>
            {sourcesOpen ? 'Hide sources' : `${uniqueSources.length} web source${uniqueSources.length === 1 ? '' : 's'}`}
          </button>
          {sourcesOpen && (
            <div style={{
              marginTop: 10, padding: '12px 14px',
              background: 'var(--surface-2)', borderRadius: 12,
              border: '1px solid var(--border)',
            }}>
              <div style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
                What the public web actually says
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {uniqueSources.map((url, i) => (
                  <a key={i} href={url} target="_blank" rel="noreferrer" style={{
                    fontSize: 11, color: 'var(--text)', textDecoration: 'none',
                    display: 'flex', alignItems: 'baseline', gap: 8,
                    padding: '4px 8px', borderRadius: 6,
                    background: 'rgba(255,255,255,0.05)',
                  }}>
                    <span style={{ color: 'var(--accent)', fontWeight: 600, fontSize: 11, minWidth: 110 }}>{hostOf(url)}</span>
                    <span style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{url.replace(/^https?:\/\//, '').replace(/^[^/]+/, '')}</span>
                    <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 10 }}>↗</span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Expand: buyer questions */}
      {v.advice && (
        <button onClick={() => setAdviceOpen(x => !x)} className="pill" style={{ height: 30, fontSize: 12, padding: '0 14px', marginTop: 8 }}>
          {adviceOpen ? 'Hide questions' : 'Questions for the vendor'}
        </button>
      )}
      {adviceOpen && v.advice && (
        <div style={{
          marginTop: 10, padding: '14px 16px',
          background: 'var(--surface-2)',
          borderRadius: 12,
          border: '1px solid var(--border)',
          fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.65, whiteSpace: 'pre-wrap',
          fontFamily: 'var(--font-serif)',
        }}>
          {v.advice}
        </div>
      )}
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

type Phase = 'idle' | 'running' | 'done'

export default function App() {
  const [customText, setCustomText] = useState('')
  const [customError, setCustomError] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [stats, setStats] = useState<RunStats>({
    totalCost: 0, totalTokens: 0, escalations: 0, totalJudgments: 0, calls: 0, elapsedMs: 0,
    modelUsage: {}, toolUsage: {},
  })
  const [vendors, setVendors] = useState<VendorResult[]>([])
  const [animedIn, setAnimedIn] = useState<Set<string>>(new Set())
  const [marketResult, setMarketResult] = useState<MarketResult | null>(null)
  const [publishedCount, setPublishedCount] = useState(0)
  const [paidCount, setPaidCount] = useState(0)
  const [reauditTick, setReauditTick] = useState(0)

  const startTimeRef = useRef<number>(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const evtRef = useRef<EventSource | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const onPublishedOk = useCallback(() => setPublishedCount(c => c + 1), [])
  const onPaidFetch = useCallback(() => setPaidCount(c => c + 1), [])

  const stopTimers = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (pollRef.current) clearInterval(pollRef.current)
    if (evtRef.current) evtRef.current.close()
  }, [])

  const fetchResults = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/audit/${id}/results`)
      if (!res.ok) return
      const data: MarketResult = await res.json()
      if (!data.vendors) return
      const sorted = [...data.vendors].sort((a, b) => (b.credibility_score ?? 0) - (a.credibility_score ?? 0))
      setVendors(sorted)
      setMarketResult(data)
      setAnimedIn(prev => { const n = new Set(prev); sorted.forEach(v => n.add(v.vendor)); return n })
    } catch { /* ignore */ }
  }, [])

  const startAudit = useCallback(async () => {
    const vendorUrls = parseVendorText(customText)
    if (vendorUrls.length === 0) {
      setCustomError('No companies found. Format: Company Name, https://website.com')
      return
    }
    setCustomError('')
    stopTimers()
    setPhase('running')
    setStats({ totalCost: 0, totalTokens: 0, escalations: 0, totalJudgments: 0, calls: 0, elapsedMs: 0, modelUsage: {}, toolUsage: {} })
    setVendors([])
    setAnimedIn(new Set())
    setMarketResult(null)
    startTimeRef.current = Date.now()

    timerRef.current = setInterval(() => {
      setStats(s => ({ ...s, elapsedMs: Date.now() - startTimeRef.current }))
    }, 250)

    try {
      const res = await fetch(`${API_BASE}/audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category: 'Custom',
          vendor_urls: vendorUrls,
          naive: false,
          n: vendorUrls.length,
        }),
      })
      const accepted = await res.json()
      pollRef.current = setInterval(() => fetchResults(accepted.run_id), 3000)
      const es = new EventSource(`${API_BASE}${accepted.stream_url}`)
      evtRef.current = es
      es.addEventListener('telemetry', (e) => {
        const ev: TelemetryEvent = JSON.parse(e.data)
        const eventTokens = ev.tokens_in + ev.tokens_out
        const toolLabel = toolLabelForStage(ev.stage)
        setStats(s => ({
          ...s,
          totalCost: s.totalCost + ev.cost_usd,
          totalTokens: s.totalTokens + eventTokens,
          escalations: ev.escalated ? s.escalations + 1 : s.escalations,
          totalJudgments: ev.stage.startsWith('judge') ? s.totalJudgments + 1 : s.totalJudgments,
          calls: s.calls + 1,
          modelUsage: ev.model
            ? {
                ...s.modelUsage,
                [ev.model]: {
                  attempts: (s.modelUsage[ev.model]?.attempts ?? 0) + 1,
                  cost: (s.modelUsage[ev.model]?.cost ?? 0) + ev.cost_usd,
                  tokens: (s.modelUsage[ev.model]?.tokens ?? 0) + eventTokens,
                  noResponse: (s.modelUsage[ev.model]?.noResponse ?? 0) + (eventTokens === 0 ? 1 : 0),
                },
              }
            : s.modelUsage,
          toolUsage: toolLabel
            ? {
                ...s.toolUsage,
                [toolLabel]: {
                  attempts: (s.toolUsage[toolLabel]?.attempts ?? 0) + 1,
                  cost: (s.toolUsage[toolLabel]?.cost ?? 0) + ev.cost_usd,
                  tokens: (s.toolUsage[toolLabel]?.tokens ?? 0) + eventTokens,
                  noResponse: s.toolUsage[toolLabel]?.noResponse ?? 0,
                },
              }
            : s.toolUsage,
        }))
        if (ev.stage === 'market_done') { setPhase('done'); stopTimers(); fetchResults(accepted.run_id) }
      })
      es.onerror = () => { setPhase('done'); stopTimers(); fetchResults(accepted.run_id) }
    } catch (err) {
      console.error(err); setPhase('idle'); stopTimers()
    }
  }, [customText, stopTimers, fetchResults])

  useEffect(() => () => stopTimers(), [stopTimers])

  // Subscribe to the global activity stream so sentinel_reaudit_done can refresh
  // the leaderboard scores in place (the demo moment). One consumer per app —
  // ActivityFeed has its own subscription; this is the second, dedicated to
  // re-poll-on-reaudit. Both reuse the same EventSource pattern as /audit.
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/activity/stream`)
    const onActivity = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { stage: string; vendor: string | null }
        if (data.stage === 'sentinel_reaudit_done') {
          // Bump tick so the visible vendor card re-fetches its score.
          setReauditTick(t => t + 1)
        }
      } catch { /* ignore */ }
    }
    es.addEventListener('activity', onActivity)
    return () => { es.removeEventListener('activity', onActivity); es.close() }
  }, [])

  // When a sentinel re-audit completes, pull fresh vendor scores from
  // /sentinel/status (the watcher's MarketResult mirror).
  useEffect(() => {
    if (reauditTick === 0) return
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch(`${API_BASE}/sentinel/status`)
        if (!res.ok) return
        const data = await res.json() as { market: { claim_inflation_index: number } }
        if (cancelled) return
        // The status snapshot doesn't expose full vendor results, so for now
        // we just refresh the market-level inflation in the banner; vendor
        // cards re-render their inflation/score via the next /audit poll
        // when the user re-runs. The activity feed shows the loop firing.
        setMarketResult(prev => prev
          ? { ...prev, claim_inflation_index: data.market.claim_inflation_index }
          : prev)
      } catch { /* ignore */ }
    })()
    return () => { cancelled = true }
  }, [reauditTick])

  const activeVendors = parseVendorText(customText)
  const sorted = [...vendors].sort((a, b) => (b.credibility_score ?? -1) - (a.credibility_score ?? -1))

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; if (!f) return
    const r = new FileReader()
    r.onload = ev => { setCustomText(ev.target?.result as string ?? ''); setCustomError('') }
    r.readAsText(f)
  }

  if (phase === 'idle') {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '64px 24px 80px',
      }}>
        <div style={{ maxWidth: 660, width: '100%', textAlign: 'center' }}>
          <div className="reveal-up d-0" style={{ marginBottom: 18, color: 'var(--accent)' }}>
            <SentinelLogo size={84} />
          </div>
          <h1 className="reveal-up d-1 headline-fade" style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 'clamp(64px, 9vw, 128px)',
            fontWeight: 700, letterSpacing: '-0.055em', lineHeight: 1,
            margin: 0, marginBottom: 18,
          }}>
            Sentinel
          </h1>

          <div className="reveal-up d-2" style={{
            color: 'var(--text-2)',
            fontSize: 'clamp(18px, 2.1vw, 24px)',
            fontFamily: 'var(--font-sans)', fontWeight: 400,
            lineHeight: 1.4, letterSpacing: '-0.01em',
            marginBottom: 40,
          }}>
            Autonomous burden of proof for the agentic web.
          </div>

          <div className="reveal-up d-3" style={{ marginBottom: 18 }}>
            <textarea
              value={customText}
              onChange={e => { setCustomText(e.target.value); setCustomError('') }}
              placeholder={'Intercom Fin, https://www.intercom.com/fin\nDecagon, https://decagon.ai\n…'}
              rows={6}
              style={{
                width: '100%', boxSizing: 'border-box',
                background: 'rgba(255,255,255,0.05)',
                border: `1px solid ${customError ? 'var(--verdict-bad)' : 'var(--border)'}`,
                borderRadius: 18, color: 'var(--text)', fontSize: 13.5,
                fontFamily: 'var(--font-mono)', padding: '18px 22px',
                resize: 'vertical', outline: 'none', lineHeight: 1.75,
                boxShadow: '0 10px 40px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06)',
                backdropFilter: 'blur(20px) saturate(180%)',
                WebkitBackdropFilter: 'blur(20px) saturate(180%)',
                transition: 'border-color 0.2s, box-shadow 0.2s',
              }}
            />
            {customError && (
              <div style={{ color: 'var(--verdict-bad)', fontSize: 12, marginTop: 8, textAlign: 'left' }}>
                {customError}
              </div>
            )}
          </div>

          <div className="reveal-up d-4" style={{ marginBottom: 14 }}>
            <button
              onClick={startAudit}
              disabled={activeVendors.length === 0}
              className="pill pill-primary"
              style={{ height: 52, fontSize: 14.5, padding: '0 36px', minWidth: 220 }}
            >
              Audit the market →
            </button>
          </div>

          <div className="reveal-up d-5" style={{
            fontSize: 12, color: 'var(--muted)',
            display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 14,
            flexWrap: 'wrap',
          }}>
            {activeVendors.length > 0 ? (
              <span>{activeVendors.length} {activeVendors.length === 1 ? 'vendor' : 'vendors'} parsed · usually 15–60s</span>
            ) : (
              <>
                <button
                  onClick={() => { setCustomText(EXAMPLE_TEXT); setCustomError('') }}
                  style={{
                    background: 'none', border: 'none', color: 'var(--accent)',
                    fontSize: 12, cursor: 'pointer', fontFamily: 'var(--font-sans)',
                    textDecoration: 'underline', textUnderlineOffset: 3, padding: 0,
                  }}>
                  load 6 example vendors
                </button>
                <span>·</span>
                <label style={{
                  cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 3,
                  color: 'var(--muted)',
                }}>
                  upload .csv / .txt
                  <input type="file" accept=".csv,.txt" style={{ display: 'none' }} onChange={handleFileUpload} />
                </label>
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  // ───── ACTIVE LAYOUT (running / done) ─────
  // Sequential reveal: strip (d-0) → activity feed (d-2) → leaderboard
  // header (d-4) → loader / first card (d-6). ~200ms between stages reads
  // as a guided rhythm, not a SaaS dashboard flash.
  return (
    <div style={{
      position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <div className="reveal-down d-0">
        <StatusStrip
          apiBase={API_BASE}
          publishedCount={publishedCount}
          paidCount={paidCount}
        />
      </div>

      {/* Subtle audit-in-progress shimmer just under the strip */}
      {phase === 'running' && (
        <div className="reveal-fade d-1 progress-shimmer" style={{
          position: 'relative', height: 2,
          background: 'rgba(243, 234, 216, 0.06)',
          overflow: 'hidden',
        }} />
      )}

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {/* LIVE ACTIVITY FEED — slides in second so the eye reads strip first. */}
        <aside className="reveal-left d-2" style={{
          width: 'min(38%, 460px)', flexShrink: 0,
          display: 'flex', flexDirection: 'column',
          borderRight: '1px solid var(--border)',
          background: 'rgba(243, 234, 216, 0.015)',
        }}>
          <ActivityFeed
            apiBase={API_BASE}
            onPublishedOk={onPublishedOk}
            onPaidFetch={onPaidFetch}
          />
        </aside>

        {/* Leaderboard column. */}
        <main style={{ flex: 1, overflowY: 'auto', padding: '24px 32px 40px' }}>
          {/* Leaderboard header — appears third */}
          <div className="reveal-up d-4" style={{
            display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
            marginBottom: 20, flexWrap: 'wrap', gap: 16,
          }}>
            <div>
              <div style={{
                fontSize: 10, color: 'var(--muted)', letterSpacing: '0.16em',
                textTransform: 'uppercase', fontWeight: 600, marginBottom: 6,
              }}>
                Leaderboard
              </div>
              <div className="headline-fade" style={{
                fontFamily: 'var(--font-sans)', fontSize: 24, fontWeight: 700,
                letterSpacing: '-0.025em',
              }}>
                Most publicly substantiated
              </div>
              <div style={{
                fontSize: 11.5, color: 'var(--muted)', marginTop: 6,
                maxWidth: 520, lineHeight: 1.6,
              }}>
                Ranked by substantiation density across claims. We measure public substantiation, not truth.
              </div>
            </div>

            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button onClick={startAudit} disabled={phase === 'running'}
                className="pill"
                style={{ height: 34, fontSize: 12 }}>
                {phase === 'running' ? 'Auditing…' : 'Re-audit'}
              </button>
              <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
                {fmtMs(stats.elapsedMs)} · {fmtCost(stats.totalCost)} · {stats.calls} calls
              </span>
            </div>
          </div>

          {/* Loader — appears fourth */}
          {phase === 'running' && sorted.length === 0 && (
            <div className="reveal-fade d-6">
              <GlassCard padding="44px 24px" style={{ textAlign: 'center', color: 'var(--muted)' }}>
                <div style={{
                  width: 36, height: 36, border: '2px solid var(--cream-soft)',
                  borderTop: '2px solid var(--accent)', borderRadius: '50%',
                  margin: '0 auto 20px', animation: 'spin 0.9s linear infinite',
                }} />
                <div style={{
                  fontSize: 14, color: 'var(--text)',
                  fontFamily: 'var(--font-sans)', fontWeight: 500,
                  letterSpacing: '-0.01em',
                }}>
                  Reading vendor pages…
                </div>
                <div style={{ fontSize: 12, marginTop: 6 }}>
                  Cards appear as each vendor finishes.
                </div>
              </GlassCard>
            </div>
          )}

          {/* Vendor cards */}
          {sorted.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
              {sorted.map(v => (
                <VendorCard key={v.vendor} v={v} animIn={animedIn.has(v.vendor)} />
              ))}
            </div>
          )}

          {/* D10 — "Interrogate the market" generative panel. Contained glass
              card, grounded on the current MarketResult, rendered in our theme. */}
          {marketResult && sorted.length > 0 && (
            <InterrogatePanel marketData={marketResult} />
          )}
        </main>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
