import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import { Panel, ErrorNote, Loading, Empty, Button, useApi } from '../components/ui'

export default function Alerts() {
  const [activeOnly, setActiveOnly] = useState(true)
  const { data, error, loading, reload } = useApi(
    () => api.alerts(activeOnly),
    [activeOnly],
  )

  return (
    <Panel
      title={`Alerts (${data?.total ?? 0})`}
      action={
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={(e) => setActiveOnly(e.target.checked)}
          />
          active only
        </label>
      }
    >
      {error && <ErrorNote message={error} onRetry={reload} />}
      {loading ? (
        <Loading />
      ) : (data?.items.length ?? 0) === 0 ? (
        <Empty>No alerts.</Empty>
      ) : (
        <ul className="space-y-2">
          {data?.items.map((a) => (
            <li key={a.alert_id} className="rounded border border-slate-800 p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                        a.severity === 'critical'
                          ? 'bg-rose-500/20 text-rose-300'
                          : a.severity === 'warning'
                            ? 'bg-amber-500/20 text-amber-300'
                            : 'bg-slate-500/20 text-slate-300'
                      }`}
                    >
                      {a.alert_type.replace(/_/g, ' ')}
                    </span>
                    {a.incident_id && (
                      <span className="font-mono text-[10px] text-slate-500">{a.incident_id}</span>
                    )}
                    {a.acknowledged && (
                      <span className="text-[10px] text-emerald-300">
                        ack by {a.acknowledged_by}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed text-slate-200">{a.message}</p>
                  <div className="mt-1 text-[10px] text-slate-500">
                    {new Date(a.created_at).toLocaleString()}
                  </div>
                </div>
                {!a.acknowledged && a.is_active && (
                  <AckButton alertId={a.alert_id} onDone={reload} />
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function AckButton({ alertId, onDone }: { alertId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            await api.acknowledgeAlert(alertId, 'Reviewed in operator console.')
            onDone()
          } catch (e) {
            setError(e instanceof ApiError ? e.message : String(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? '…' : 'Acknowledge'}
      </Button>
      {error && <span className="text-[10px] text-rose-300">{error}</span>}
    </div>
  )
}

/**
 * Read-only assistant.
 *
 * The input is deliberately not styled as a command box, and the answer panel
 * always renders the citations and disclaimer. The backend refuses operational
 * requests; showing that refusal plainly is part of the point.
 */
export function Assistant() {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [answer, setAnswer] = useState<{
    answer: string
    citations: { label: string; detail: string }[]
    disclaimer?: string
  } | null>(null)
  const suggestions = useApi(() => api.chatSuggestions())
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [answer])

  async function send(text: string) {
    const q = text.trim()
    if (!q || busy) return
    setBusy(true)
    setError(null)
    try {
      setAnswer(await api.chat(q))
      setMessage('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel title="Ask about the current situation" className="lg:col-span-2">
        <div ref={logRef} className="min-h-[8rem]">
          {answer ? (
            <div>
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">
                {answer.answer}
              </div>
              {answer.citations.length > 0 && (
                <div className="mt-3 border-t border-slate-800 pt-2">
                  <div className="text-xs font-semibold text-slate-400">Sources</div>
                  <ul className="mt-1 space-y-0.5">
                    {answer.citations.map((c, i) => (
                      <li key={i} className="text-[11px] text-slate-500">
                        <span className="text-slate-400">{c.label}</span> — {c.detail}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {answer.disclaimer && (
                <p className="mt-3 rounded border border-sky-500/20 bg-sky-500/5 p-2 text-[11px] text-sky-200">
                  {answer.disclaimer}
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-slate-500">
              The assistant reads the database and explains it. It cannot approve plans, dispatch
              resources, or order evacuations.
            </p>
          )}
        </div>

        {error && (
          <div className="mt-3">
            <ErrorNote message={error} />
          </div>
        )}

        <div className="mt-3 flex gap-2">
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send(message)}
            placeholder="e.g. How many incidents are unverified?"
            className="flex-1 rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
          />
          <Button variant="primary" onClick={() => send(message)} disabled={busy || !message.trim()}>
            {busy ? '…' : 'Ask'}
          </Button>
        </div>
      </Panel>

      <Panel title="Try asking">
        <ul className="space-y-1.5">
          {(suggestions.data?.suggestions ?? []).map((s, i) => (
            <li key={i}>
              <button
                onClick={() => send(s)}
                className="w-full rounded border border-slate-800 px-2 py-1.5 text-left text-xs text-slate-300 hover:border-sky-500/50 hover:text-sky-200"
              >
                {s}
              </button>
            </li>
          ))}
        </ul>
        {suggestions.data?.note && (
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            {suggestions.data.note}
          </p>
        )}
      </Panel>
    </div>
  )
}

export function Reports() {
  const reports = useApi(() => api.reports())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function generate() {
    setBusy(true)
    setError(null)
    try {
      await api.generateReport()
      reports.reload()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title={`Situation reports (${reports.data?.total ?? 0})`}
      action={
        <Button variant="primary" onClick={generate} disabled={busy}>
          {busy ? 'Generating…' : 'Generate report'}
        </Button>
      }
    >
      {error && <ErrorNote message={error} />}
      {reports.loading ? (
        <Loading />
      ) : (reports.data?.items.length ?? 0) === 0 ? (
        <Empty>No reports generated.</Empty>
      ) : (
        <ul className="space-y-2">
          {reports.data?.items.map((r) => (
            <li key={r.report_id} className="rounded border border-slate-800 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-200">{r.title}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-slate-500">
                    {new Date(r.created_at).toLocaleString()}
                  </span>
                  <a
                    href={api.reportPdfUrl(r.report_id)}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:border-sky-500 hover:text-sky-200"
                  >
                    PDF
                  </a>
                </div>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-slate-400">{r.summary}</p>
              {r.uncertainties?.length > 0 && (
                <div className="mt-2">
                  <div className="text-[11px] font-semibold text-orange-300">
                    Stated uncertainties
                  </div>
                  <ul className="mt-0.5 space-y-0.5">
                    {r.uncertainties.map((u, i) => (
                      <li key={i} className="text-[11px] text-slate-500">
                        • {u}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}