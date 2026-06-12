/**
 * Sentinel mark — concentric radar arcs over a single pulse dot.
 *
 * Reads as: a watcher broadcasting outward. The shape is the product —
 * autonomous monitoring of vendor claims rippling out to the agentic web.
 *
 * Designed for monochrome (uses currentColor) so it works on dark glass and
 * on the wordmark, and scales clean from 16px (favicon) to 120px (hero).
 */
export function SentinelLogo({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      stroke="currentColor"
      aria-label="Sentinel"
      role="img"
      className={className}
    >
      {/* Three radar arcs — outermost faintest, mid normal, inner brightest */}
      <path
        d="M 16 72 A 34 34 0 0 1 84 72"
        strokeWidth="6"
        strokeLinecap="round"
        opacity="0.35"
      />
      <path
        d="M 28 72 A 22 22 0 0 1 72 72"
        strokeWidth="6.5"
        strokeLinecap="round"
        opacity="0.7"
      />
      <path
        d="M 40 72 A 10 10 0 0 1 60 72"
        strokeWidth="7"
        strokeLinecap="round"
      />
      {/* Pulse dot — the watcher */}
      <circle cx="50" cy="72" r="4.5" fill="currentColor" />
    </svg>
  );
}
