import { useEffect, useRef, useState } from 'react'

interface ActivityEvent {
  stage: string
  vendor: string | null
  model: string | null
  claim_id: string | null
  cost_usd: number
  latency_ms: number
  escalated: boolean
  tokens_in: number
  tokens_out: number
  payload?: Record<string, unknown>
}

interface FeedLine {
  id: number
  ts: number
  stage: string
  icon: string
  text: string
  detail: string | null
  muted: boolean
  accent: 'neutral' | 'good' | 'warn' | 'bad' | 'indigo'
  escalated: boolean
}

const STAGE_PRETTY: Record<string, { icon: string; label: (e: ActivityEvent) => string }> = {
  sentinel_trigger: { icon: '⟳', label: e => `${e.vendor ?? 'vendor'} changed — re-auditing` },
  ingest: { icon: '·', label: e => `scraping ${e.vendor ?? ''}`.trim() },
  extract: { icon: '·', label: () => 'extracting claims' },
  hunt: { icon: '·', label: () => 'hunting evidence' },
  judge_cheap: { icon: '·', label: e => `cheap judge${e.claim_id ? ` · ${shortClaim(e.claim_id)}` : ''}` },
  judge_premium: { icon: '↑', label: e => `escalated to premium${e.claim_id ? ` · ${shortClaim(e.claim_id)}` : ''}` },
  advise: { icon: '·', label: () => 'advise' },
  vendor_done: { icon: '✓', label: e => `${e.vendor ?? 'vendor'} done` },
  sentinel_published: { icon: '↗', label: e => describePublished(e) },
  sentinel_notified: { icon: '✉', label: e => describeNotified(e) },
  sentinel_reaudit_done: { icon: '✓', label: e => describeReaudit(e) },
}

function shortClaim(id: string): string {
  if (id.length <= 10) return `claim ${id}`
  return `claim ${id.slice(0, 8)}…`
}

function describePublished(e: ActivityEvent): string {
  const status = e.payload?.status as string | undefined
  if (status === 'ok') return 'published to cited.md'
  if (status === 'skipped:no_key') return 'publish skipped (no SENSO key)'
  if (status === 'skipped:no_geo_question') return 'publish skipped (no geo_question_id)'
  if (status?.startsWith('skipped')) return `publish skipped (${status.slice(8)})`
  return 'publish attempted'
}

function describeNotified(e: ActivityEvent): string {
  const status = e.payload?.status as string | undefined
  if (status === 'ok') return 'notified via Composio'
  if (status?.startsWith('skipped')) return 'notify skipped (no Composio key)'
  return 'notify attempted'
}

function describeReaudit(e: ActivityEvent): string {
  const oldS = e.payload?.old_score as number | undefined
  const newS = e.payload?.new_score as number | undefined
  if (typeof oldS === 'number' && typeof newS === 'number') {
    const oldPct = Math.round(oldS * 100)
    const newPct = Math.round(newS * 100)
    const arrow = newPct < oldPct ? '↓' : newPct > oldPct ? '↑' : '→'
    return `${e.vendor ?? 'vendor'} score ${oldPct}% ${arrow} ${newPct}%`
  }
  return `${e.vendor ?? 'vendor'} re-audit done`
}

function accentFor(stage: string, escalated: boolean, muted: boolean): FeedLine['accent'] {
  if (muted) return 'neutral'
  if (stage === 'sentinel_trigger') return 'indigo'
  if (stage === 'sentinel_reaudit_done') return 'good'
  if (stage === 'sentinel_published') return 'good'
  if (stage === 'judge_premium' || escalated) return 'warn'
  if (stage === 'vendor_done') return 'good'
  return 'neutral'
}

function isMuted(e: ActivityEvent): boolean {
  const status = e.payload?.status as string | undefined
  return Boolean(status && status.startsWith('skipped'))
}

function fmtTime(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

const MAX_LINES = 80

export function ActivityFeed({
  apiBase,
  onPublishedOk,
  onPaidFetch,
}: {
  apiBase: string
  onPublishedOk: () => void
  onPaidFetch: () => void
}) {
  const [lines, setLines] = useState<FeedLine[]>([])
  const [connected, setConnected] = useState(false)
  const idRef = useRef(0)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    // Reuse the same EventSource pattern App.tsx already uses for /audit
    // streams. ONE consumer for /activity/stream; no new SSE plumbing.
    let cancelled = false
    let retryHandle: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      const es = new EventSource(`${apiBase}/activity/stream`)
      esRef.current = es

      es.addEventListener('open', () => { if (!cancelled) setConnected(true) })
      es.addEventListener('activity', (ev: MessageEvent) => {
        try {
          const data: ActivityEvent = JSON.parse(ev.data)
          const pretty = STAGE_PRETTY[data.stage]
          if (!pretty) return
          const muted = isMuted(data)
          const line: FeedLine = {
            id: ++idRef.current,
            ts: Date.now(),
            stage: data.stage,
            icon: pretty.icon,
            text: pretty.label(data),
            detail: data.model ?? null,
            muted,
            accent: accentFor(data.stage, data.escalated, muted),
            escalated: data.escalated,
          }
          setLines(prev => [line, ...prev].slice(0, MAX_LINES))
          if (data.stage === 'sentinel_published' && !muted) onPublishedOk()
          if (data.stage === 'paid_fetch') onPaidFetch()
        } catch { /* ignore malformed */ }
      })

      es.onerror = () => {
        if (cancelled) return
        setConnected(false)
        es.close()
        // simple reconnect after 2s (acceptance: "SSE reconnects if dropped")
        retryHandle = setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      cancelled = true
      if (retryHandle) clearTimeout(retryHandle)
      esRef.current?.close()
    }
  }, [apiBase, onPublishedOk, onPaidFetch])

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0,
    }}>
      <div style={{
        padding: '14px 22px 10px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        borderBottom: '1px solid var(--border)',
        background: 'rgba(255,255,255,0.02)',
      }}>
        <div style={{
          fontSize: 11, fontWeight: 600, letterSpacing: '0.14em',
          textTransform: 'uppercase', color: 'var(--text-2)',
        }}>
          Live activity
        </div>
        <div style={{
          fontSize: 10, color: connected ? 'var(--verdict-good)' : 'var(--muted)',
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{
            display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
            background: connected ? 'var(--verdict-good)' : 'var(--muted)',
          }} />
          {connected ? 'connected' : 'reconnecting…'}
        </div>
      </div>

      <div style={{
        flex: 1, overflowY: 'auto', padding: '8px 16px 24px',
        display: 'flex', flexDirection: 'column', gap: 6,
      }}>
        {lines.length === 0 && (
          <div style={{
            color: 'var(--muted)', fontSize: 12, padding: '36px 12px',
            textAlign: 'center', fontStyle: 'italic',
          }}>
            Watching for events…
          </div>
        )}
        {lines.map(line => (
          <FeedLineRow key={line.id} line={line} />
        ))}
      </div>
    </div>
  )
}

function colorOf(accent: FeedLine['accent']): string {
  switch (accent) {
    case 'good': return 'var(--verdict-good)'
    case 'warn': return 'var(--verdict-warn)'
    case 'bad': return 'var(--verdict-bad)'
    case 'indigo': return 'var(--accent)'
    default: return 'var(--text-2)'
  }
}

function FeedLineRow({ line }: { line: FeedLine }) {
  return (
    <div className="feed-line-in" style={{
      display: 'grid',
      gridTemplateColumns: '60px 18px 1fr auto',
      gap: 10, alignItems: 'baseline',
      padding: '8px 12px',
      background: line.muted ? 'transparent' : 'rgba(255,255,255,0.025)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      opacity: line.muted ? 0.55 : 1,
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 10,
        color: 'var(--muted)', letterSpacing: '-0.01em',
        fontVariantNumeric: 'tabular-nums',
      }}>
        {fmtTime(line.ts)}
      </div>
      <div style={{
        color: colorOf(line.accent), fontWeight: 600, fontSize: 12, lineHeight: 1,
      }}>
        {line.icon}
      </div>
      <div style={{
        fontSize: 12.5, color: 'var(--text)', lineHeight: 1.35,
        letterSpacing: '-0.005em',
      }}>
        {line.text}
      </div>
      {line.detail && (
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9.5,
          color: 'var(--muted)', whiteSpace: 'nowrap',
        }}>
          {line.detail}
        </div>
      )}
    </div>
  )
}
