import { useState } from 'react'
import { api, ApiError } from '../api'
import { Panel, ErrorNote, Loading, Empty, Button, useApi } from '../components/ui'
import { ProvenanceBadge, StatusBadge } from '../components/Badges'
import type { Road } from '../types'

export default function Roads() {
  const roads = useApi(() => api.roads())

  return (
    <div className="space-y-4">
      <Panel title="Road conditions">
        <p className="mb-3 text-xs text-slate-400">
          A road with no condition report is shown as <em>unknown</em>, never as open. The backend
          decides what counts as authoritative; the console only submits the claim and its source.
        </p>
        {roads.error && <ErrorNote message={roads.error} onRetry={roads.reload} />}
        {roads.loading ? (
          <Loading />
        ) : (roads.data?.items.length ?? 0) === 0 ? (
          <Empty>No roads recorded.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">Road</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Provenance</th>
                  <th className="py-2 pr-3">Source</th>
                  <th className="py-2 pr-3">Passable</th>
                  <th className="py-2">Report</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {roads.data?.items.map((r) => (
                  <tr key={r.road_id ?? r.name}>
                    <td className="py-2.5 pr-3 text-slate-200">
                      {r.name}
                      {r.road_type && (
                        <div className="text-[10px] text-slate-500">{r.road_type}</div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <ProvenanceBadge status={r.fact_status} />
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-slate-400">{r.source ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-xs">
                      <span className={r.passable ? 'text-emerald-300' : 'text-rose-300'}>
                    {r.passable ? 'yes' : 'no'}
                    </span>
                    </td>
                    <td className="py-2.5">
                      <RoadConditionForm road={r} onDone={roads.reload} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

const STATUSES = ['open', 'partially_blocked', 'closed', 'impassable', 'unknown']

function RoadConditionForm({ road, onDone }: { road: Road; onDone: () => void }) {
  const [status, setStatus] = useState(road.status)
  const reportedBy = 'chief.morales'
  const [source, setSource] = useState('field_patrol')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    if (!road.road_id) return
    setBusy(true)
    setMsg(null)
    try {
      await api.reportRoadCondition(road.road_id, {
        status,
        reported_by: reportedBy,
        source,
        blocked_reason: reason || undefined,
      })
      setMsg({ ok: true, text: 'recorded' })
      onDone()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex items-center gap-1">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-xs"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-xs"
          title="Who is reporting. The backend judges authority from this."
        >
          <option value="field_patrol">field patrol</option>
          <option value="police">police</option>
          <option value="fire_service">fire service</option>
          <option value="citizen_report">citizen report</option>
          <option value="satellite">satellite</option>
        </select>
      </div>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="reason (if blocked)"
        className="w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-xs"
      />
      <Button onClick={submit} disabled={busy || !road.road_id}>
        {busy ? '…' : 'Submit'}
      </Button>
      {msg && (
        <span className={`text-[10px] ${msg.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
          {msg.text}
        </span>
      )}
      {!road.evidence && (
        <span className="text-[10px] text-slate-500">no evidence on file</span>
      )}
    </div>
  )
}