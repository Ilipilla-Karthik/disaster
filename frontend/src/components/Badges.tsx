/**
 * Provenance and status display.
 *
 * The single most important rule in this UI: an unverified claim must never be
 * rendered the same way as a confirmed one. `ProvenanceBadge` is the only
 * component allowed to colour-code fact status, so the meaning stays consistent.
 */
import type { FactStatus } from '../types'

const FACT_STYLES: Record<FactStatus, { label: string; className: string; hint: string }> = {
  known: {
    label: 'Verified',
    className: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
    hint: 'Confirmed by an authoritative source.',
  },
  inferred: {
    label: 'Inferred',
    className: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
    hint: 'Derived from other data, not directly reported. Treat as a precaution.',
  },
  unverified: {
    label: 'Unverified',
    className: 'bg-orange-500/15 text-orange-300 ring-orange-500/30',
    hint: 'Reported but not confirmed by an authoritative source.',
  },
  unknown: {
    label: 'Unknown',
    className: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
    hint: 'No condition report exists. This is not evidence that the road is open.',
  },
}

export function ProvenanceBadge({ status }: { status: FactStatus | string }) {
  const key = (status ?? 'unknown') as FactStatus
  const style = FACT_STYLES[key] ?? {
    label: String(status ?? 'unknown'),
    className: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
    hint: 'Unrecognised provenance value.',
  }
  return (
    <span
      title={style.hint}
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style.className}`}
    >
      {style.label}
    </span>
  )
}

export function SeverityBadge({ level }: { level?: string | null }) {
  if (!level) return null
  // "verification_required" means the data is too weak to band at all. It is
  // deliberately the most visually prominent state, not a neutral grey.
  if (level === 'verification_required') {
    return (
      <span
        title="Not enough verified information to assign a severity. Needs field verification."
        className="inline-flex items-center rounded bg-orange-500/20 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-orange-300 ring-1 ring-inset ring-orange-500/40"
      >
        Verify first
      </span>
    )
  }
  const styles: Record<string, string> = {
    low: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
    moderate: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
    high: 'bg-orange-500/15 text-orange-300 ring-orange-500/30',
    critical: 'bg-rose-500/20 text-rose-300 ring-rose-500/40',
  }
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium uppercase tracking-wide ring-1 ring-inset ${
        styles[level] ?? 'bg-slate-500/15 text-slate-300 ring-slate-500/30'
      }`}
    >
      {level}
    </span>
  )
}

const STATUS_STYLES: Record<string, string> = {
  available: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  reserved: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  deployed: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
  en_route: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
  returning: 'bg-indigo-500/15 text-indigo-300 ring-indigo-500/30',
  unavailable: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
  maintenance: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',

  draft: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
  awaiting_approval: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  approved: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
  active: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  rejected: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
  superseded: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',

  pending: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
  in_progress: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
  completed: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  cancelled: 'bg-slate-500/15 text-slate-400 ring-slate-500/30',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        STATUS_STYLES[status] ?? 'bg-slate-500/15 text-slate-300 ring-slate-500/30'
      }`}
    >
      {status.replace(/_/g, ' ')}
    </span>
  )
}

/**
 * A capacity bar. Over-capacity is drawn as a distinct hatched state rather than
 * a longer bar, so an overflow can never read as "more room available".
 */
export function CapacityBar({
  used,
  total,
  label,
}: {
  used: number
  total: number
  label?: string
}) {
  const pct = total > 0 ? (used / total) * 100 : 0
  const over = used > total
  const near = !over && pct >= 85
  return (
    <div>
      {label && (
        <div className="mb-1 flex justify-between text-xs text-slate-400">
          <span>{label}</span>
          <span className={over ? 'font-semibold text-rose-300' : near ? 'text-amber-300' : ''}>
            {used} / {total}
            {over ? ' (over capacity)' : near ? ' (near capacity)' : ''}
          </span>
        </div>
      )}
      <div className="h-2 w-full overflow-hidden rounded bg-slate-700/50">
        <div
          className={`h-full ${over ? 'bg-rose-500' : near ? 'bg-amber-400' : 'bg-emerald-500'}`}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
    </div>
  )
}