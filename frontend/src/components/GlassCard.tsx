import { CSSProperties, ReactNode } from 'react'

/**
 * The Sentinel glass primitive. Every panel renders through here so the design
 * contract (rgba(18,18,30,0.55), blur(14-20px) saturate(150%), 1px hairline,
 * soft deep shadow) lives in ONE place. D10's C1 card mounts inside this and
 * inherits containment for free.
 */
export function GlassCard({
  children,
  padding = '20px 22px',
  radius = 18,
  style,
  className,
  title,
}: {
  children: ReactNode
  padding?: CSSProperties['padding']
  radius?: number
  style?: CSSProperties
  className?: string
  title?: string
}) {
  return (
    <div
      title={title}
      className={`glass ${className ?? ''}`.trim()}
      style={{
        borderRadius: radius,
        padding,
        ...style,
      }}
    >
      {children}
    </div>
  )
}
