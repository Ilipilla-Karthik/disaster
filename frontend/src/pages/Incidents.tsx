import { useState } from 'react'
import { api, ApiError } from '../api'
import { Panel, ErrorNote, Loading, Empty, Button, useApi } from '../components/ui'
import { SeverityBadge } from '../components/Badges'
import { MapView, type MapPoint } from '../components/MapView'
import type { Incident } from '../types'

export default function Incidents() {
  const [showDuplicate, setShowDuplicate] = useState(true)
  const list = useApi(
    () => api.incidents({ include_duplicates: showDuplicate, limit: 100 }),
    [showDuplicate],
  )

  const points: MapPoint[] = (list.data?.items ?? []).map((i) => ({
    id: i.incident_id,
    label: `${i.location} · ${i.incident_type.replace(/_/g, ' ')}`,
    latitude: i.latitude ?? Number.NaN,
    longitude: i.longitude ?? Number.NaN,
    detail: i.description ?? undefined,
    kind: 'incident' as const,
  }))

  return (
    <div className="space-y-4">
      <IntakeForm onSubmitted={list.reload} />

      <Panel
        title={`Incidents (${list.data?.total ?? 0})`}
        action={
          <label className="flex items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={showDuplicate}
              onChange={(e) => setShowDuplicate(e.target.checked)}
            />
            show duplicate-linked reports
          </label>
        }
      >
        {list.error && <ErrorNote message={list.error} onRetry={list.reload} />}
        {list.loading ? (
          <Loading />
        ) : (list.data?.items.length ?? 0) === 0 ? (
          <Empty>No incidents reported.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">ID</th>
                  <th className="py-2 pr-3">Type</th>
                  <th className="py-2 pr-3">Location</th>
                  <th className="py-2 pr-3">Severity</th>
                  <th className="py-2 pr-3 text-right">Affected</th>
                  <th className="py-2 pr-3 text-right">Priority</th>
                  <th className="py-2">Unresolved</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {list.data?.items.map((i) => (
                  <tr key={i.incident_id} className="align-top">
                    <td className="py-2.5 pr-3 font-mono text-xs text-slate-400">
                      {i.incident_id}
                      {i.is_duplicate && (
                        <div
                          className="mt-1 text-[10px] text-purple-300"
                          title={`Linked to ${i.duplicate_of}`}
                        >
                          dup of {i.duplicate_of}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-200">
                      {i.incident_type.replace(/_/g, ' ')}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-300">
                      {i.location}
                      {i.latitude == null && (
                        <div className="text-[10px] text-orange-300">no verified position</div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <SeverityBadge level={i.severity_level ?? i.severity} />
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-slate-200">
                      {i.people_reported_affected}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-slate-300">
                      {i.priority_score?.toFixed(1) ?? '—'}
                    </td>
                    <td className="py-2.5 text-xs text-orange-300/80">
                      {i.missing_fields?.length ? i.missing_fields.join(', ') : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Map">
        <MapView points={points} />
      </Panel>
    </div>
  )
}

function IntakeForm({ onSubmitted }: { onSubmitted: () => void }) {
  const [rawText, setRawText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Incident | null>(null)

  async function submit() {
    if (!rawText.trim()) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const incident = await api.submitIncident({ raw_text: rawText.trim() })
      setResult(incident)
      setRawText('')
      onSubmitted()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Report an incident">
      <p className="mb-3 text-xs text-slate-400">
        Free text is fine. Anything the system cannot determine is marked unresolved rather than
        guessed, and an unverified report is never given a confident severity.
      </p>
      <textarea
        value={rawText}
        onChange={(e) => setRawText(e.target.value)}
        rows={3}
        placeholder="e.g. Flooding on Riverside Avenue, about 40 people trapped near the bridge."
        className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
      />
      <div className="mt-2 flex items-center gap-2">
        <Button variant="primary" onClick={submit} disabled={busy || !rawText.trim()}>
          {busy ? 'Submitting…' : 'Submit report'}
        </Button>
      </div>

      {error && (
        <div className="mt-3">
          <ErrorNote message={error} />
        </div>
      )}

      {result && (
        <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-slate-400">{result.incident_id}</span>
            <SeverityBadge level={result.severity_level ?? result.severity} />
            {result.is_duplicate && (
              <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-[10px] text-purple-300">
                possible duplicate of {result.duplicate_of}
              </span>
            )}
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-slate-500">Type</dt>
              <dd className="text-slate-200">{result.incident_type.replace(/_/g, ' ')}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Affected</dt>
              <dd className="text-slate-200">{result.people_reported_affected}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Extraction</dt>
              <dd className="text-slate-200">
                {result.intake?.extraction_method ?? 'unknown'}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Unresolved</dt>
              <dd className="text-orange-300">
                {result.missing_fields?.length ? result.missing_fields.join(', ') : 'none'}
              </dd>
            </div>
          </dl>
          {result.intake?.notes?.length ? (
            <ul className="mt-2 space-y-0.5 text-[11px] text-slate-500">
              {result.intake.notes.map((n, k) => (
                <li key={k}>• {n}</li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </Panel>
  )
}