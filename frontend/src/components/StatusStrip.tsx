import { useEffect, useState } from 'react'
import { SentinelLogo } from './SentinelLogo'

interface WatchVendor {
  vendor: string
  url: string
  is_test: boolean
  last_audit_at: number | null
  observed: boolean
}

interface SentinelStatus {
  watching: number
  watch_enabled: boolean
  watch_interval_s: number
  last_check_ts: number | null
  triggers_count: number
  task_running: boolean
  vendors: WatchVendor[]
  market: {
    category: string
    n_vendors_audited: number
    claim_inflation_index: number
  }
}

function fmtAgo(ts: number | null): string {
  if (ts === null) return 'never'
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

export function StatusStrip({
  apiBase,
  publishedCount,
  paidCount,
}: {
  apiBase: string
  publishedCount: number
  paidCount: number
}) {
  const [status, setStatus] = useState<SentinelStatus | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const res = await fetch(`${apiBase}/sentinel/status`)
        if (!res.ok) return
        const data: SentinelStatus = await res.json()
        if (alive) setStatus(data)
      } catch { /* ignore */ }
    }
    poll()
    const id = setInterval(poll, 2500)
    return () => { alive = false; clearInterval(id) }
  }, [apiBase])

  // Re-render once per second so "last check" age stays fresh without polling.
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])
  void tick

  const inflation = status?.market.claim_inflation_index ?? 0
  const inflationLabel = inflation > 0 ? `${inflation.toFixed(2)}×` : '—'
  const watching = status?.watching ?? 0
  const watchEnabled = status?.watch_enabled ?? false
  const taskRunning = status?.task_running ?? false
  const isLive = watchEnabled && taskRunning

  return (
    <div style={{
      position: 'sticky', top: 0, zIndex: 30,
      background: 'linear-gradient(180deg, rgba(11,11,18,0.78) 0%, rgba(11,11,18,0.60) 100%)',
      backdropFilter: 'blur(22px) saturate(170%)',
      WebkitBackdropFilter: 'blur(22px) saturate(170%)',
      borderBottom: '1px solid var(--border)',
      padding: '14px 28px',
      display: 'grid',
      gridTemplateColumns: 'auto 1fr auto',
      alignItems: 'center',
      gap: 24,
    }}>
      {/* Brand — reveals first inside the strip */}
      <div className="reveal-fade" style={{
        display: 'flex', alignItems: 'center', gap: 12, minWidth: 220,
      }}>
        <div style={{ color: 'var(--accent)' }}>
          <SentinelLogo size={26} />
        </div>
        <div>
          <div className="headline-fade" style={{
            fontFamily: 'var(--font-sans)', fontSize: 19, fontWeight: 700,
            letterSpacing: '-0.03em', lineHeight: 1,
          }}>
            Sentinel
          </div>
          <div style={{
            fontSize: 10.5, color: 'var(--muted)', marginTop: 4,
            letterSpacing: '-0.005em',
          }}>
            Autonomous burden of proof for the agentic web.
          </div>
        </div>
      </div>

      {/* Hero: market inflation — reveals second */}
      <div
        className="reveal-fade"
        style={{
          display: 'flex', alignItems: 'baseline', gap: 14, justifyContent: 'center',
          animationDelay: '0.18s',
        }}
        title="Market inflation: total claims per publicly substantiated claim. Higher = more self-reported marketing copy. We measure public substantiation, not truth."
      >
        <div style={{
          fontSize: 10, color: 'var(--muted)', letterSpacing: '0.16em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>
          Market inflation
        </div>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 700,
          fontSize: 32, lineHeight: 1, letterSpacing: '-0.04em',
          color: inflation >= 2 ? 'var(--verdict-warn)' : 'var(--cream)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {inflationLabel}
        </div>
        {status?.market.category && (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            {status.market.category}
          </div>
        )}
      </div>

      {/* Counters — reveal last so the eye finishes on live state */}
      <div className="reveal-fade" style={{
        display: 'flex', alignItems: 'center', gap: 22,
        fontFamily: 'var(--font-sans)',
        animationDelay: '0.36s',
      }}>
        <CounterCell
          label={isLive ? 'Watching' : 'Idle'}
          value={String(watching)}
          pulse={isLive}
          title={watchEnabled
            ? (taskRunning ? `Watch loop ticks every ${status?.watch_interval_s}s.` : 'Watch enabled but loop not running.')
            : 'WATCH_ENABLED=false. Set it to start the autonomous loop.'}
        />
        <CounterCell
          label="Last check"
          value={fmtAgo(status?.last_check_ts ?? null)}
          title="Seconds since the watcher last fetched any vendor page."
        />
        <CounterCell
          label="Triggers"
          value={String(status?.triggers_count ?? 0)}
          title="Autonomous re-audits fired since boot (claim hash changed)."
        />
        <CounterCell
          label="Published"
          value={String(publishedCount)}
          muted={publishedCount === 0}
          title={publishedCount === 0
            ? 'Will populate when SENSO_API_KEY lands and publishes flip from skipped to ok.'
            : 'Audits published to cited.md this session.'}
        />
        <CounterCell
          label="Agents paid"
          value={String(paidCount)}
          muted={paidCount === 0}
          title={paidCount === 0
            ? 'Will tick when D06 buyer agent pays through x402.'
            : 'Paid verdict fetches via x402 this session.'}
        />
      </div>
    </div>
  )
}

function CounterCell({
  label, value, title, muted, pulse,
}: { label: string; value: string; title?: string; muted?: boolean; pulse?: boolean }) {
  return (
    <div title={title} style={{
      display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
      cursor: title ? 'help' : 'default',
    }}>
      <div style={{
        fontSize: 9.5, color: 'var(--muted)', letterSpacing: '0.14em',
        textTransform: 'uppercase', fontWeight: 600,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {pulse && (
          <span className="sentinel-pulse" style={{
            display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
            background: 'var(--accent)',
          }} />
        )}
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 14,
        color: muted ? 'var(--muted)' : 'var(--text)',
        letterSpacing: '-0.01em', lineHeight: 1.3, marginTop: 3,
        fontVariantNumeric: 'tabular-nums',
      }}>
        {value}
      </div>
    </div>
  )
}
