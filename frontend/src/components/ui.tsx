/**
 * Shared UI primitives: cards, panels, empty/error states and a small data hook.
 *
 * `useApi` exists so every page handles loading, error and refresh the same way.
 * In an operations console a silent failure is dangerous: a stale count that
 * looks live is worse than an obvious error.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ApiError } from '../api'

export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const reload = useCallback(() => {
    setLoading(true)
    fn()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // fn is intentionally excluded: callers pass inline closures, and deps
    // control when a reload happens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    reload()
  }, [reload])

  return { data, error, loading, reload, setData }
}

export function Panel({
  title,
  action,
  children,
  className = '',
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={`rounded-lg border border-slate-800 bg-slate-900/60 shadow-sm ${className}`}
    >
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-slate-200">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Stat({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'warn' | 'bad' | 'good'
}) {
  const tones: Record<string, string> = {
    default: 'text-slate-100',
    warn: 'text-amber-300',
    bad: 'text-rose-300',
    good: 'text-emerald-300',
  }
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tones[tone]}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  )
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="rounded border border-rose-400/40 px-2 py-0.5 text-xs hover:bg-rose-500/20"
        >
          Retry
        </button>
      )}
    </div>
  )
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded bg-slate-800/60" />
      ))}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-slate-500">{children}</p>
}

/**
 * The persistent reminder that this system recommends and humans decide.
 * Rendered on every page that can lead to an operational action.
 */
export function SafetyBanner({ disclaimer }: { disclaimer?: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-sky-500/25 bg-sky-500/10 px-3 py-2 text-xs text-sky-200">
      <span aria-hidden="true">⚠</span>
      <span>
        {disclaimer ??
          'Decision-support only. Plans are recommendations; deployment requires an authorised commander to approve and start each action.'}
      </span>
    </div>
  )
}

export function Button({
  children,
  onClick,
  variant = 'default',
  disabled,
  type = 'button',
  title,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'danger' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
  title?: string
}) {
  const variants: Record<string, string> = {
    default:
      'border border-slate-700 bg-slate-800 text-slate-200 hover:bg-slate-700 disabled:opacity-40',
    primary:
      'border border-sky-500 bg-sky-600 text-white hover:bg-sky-500 disabled:opacity-40',
    danger:
      'border border-rose-500/50 bg-rose-600/80 text-white hover:bg-rose-600 disabled:opacity-40',
    ghost: 'text-slate-400 hover:text-slate-200 disabled:opacity-40',
  }
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`rounded px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed ${variants[variant]}`}
    >
      {children}
    </button>
  )
}