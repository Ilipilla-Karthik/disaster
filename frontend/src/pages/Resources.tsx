import { useState } from 'react'
import { api, ApiError } from '../api'
import { Panel, ErrorNote, Loading, Empty, Button, Stat, useApi } from '../components/ui'
import { StatusBadge, CapacityBar } from '../components/Badges'

export default function Resources() {
  const [filter, setFilter] = useState('')
  const summary = useApi(() => api.resourceSummary())
  const list = useApi(
    () => api.resources(filter ? { status: filter, limit: 200 } : { limit: 200 }),
    [filter],
  )

  return (
    <div className="space-y-4">
      {summary.data && (
        <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
          {(
            [
              ['available', 'Available'],
              ['reserved', 'Reserved'],
              ['deployed', 'Deployed'],
              ['returning', 'Returning'],
              ['unavailable', 'Unavailable'],
              ['maintenance', 'Maintenance'],
            ] as const
          ).map(([key, label]) => (
            <Stat
              key={key}
              label={label}
              value={summary.data?.[key] ?? 0}
              tone={
                key === 'deployed' && (summary.data?.[key] ?? 0) > 0
                  ? 'warn'
                  : key === 'available'
                    ? 'good'
                    : 'default'
              }
            />
          ))}
        </div>
      )}

      <Panel
        title={`Resources (${list.data?.total ?? 0})`}
        action={
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200"
          >
            <option value="">all states</option>
            {Object.keys(summary.data?.state_machine ?? {}).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        }
      >
        {list.error && <ErrorNote message={list.error} onRetry={list.reload} />}
        {list.loading ? (
          <Loading />
        ) : (list.data?.items.length ?? 0) === 0 ? (
          <Empty>No resources in this state.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">ID</th>
                  <th className="py-2 pr-3">Type</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3 text-right">Capacity</th>
                  <th className="py-2 pr-3">Location</th>
                  <th className="py-2 pr-3">Assignment</th>
                  <th className="py-2">Transition</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {list.data?.items.map((r) => (
                  <tr key={r.resource_id}>
                    <td className="py-2.5 pr-3 font-mono text-xs text-slate-400">
                      {r.resource_id}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-200">
                      {r.resource_type.replace(/_/g, ' ')}
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-slate-300">
                      {r.capacity ?? '—'}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-400">{r.location ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-xs text-slate-400">
                      {r.current_assignment ?? '—'}
                    </td>
                    <td className="py-2.5">
                      <TransitionControl resourceId={r.resource_id} current={r.status} />
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

/**
 * Only offers transitions the backend itself declares legal for the current
 * state. The UI must never present an action the state machine will refuse -
 * the operator would just learn the boundary by hitting an error.
 */
function TransitionControl({
  resourceId,
  current,
}: {
  resourceId: string
  current: string
}) {
  const summary = useApi(() => api.resourceSummary())
  const allowed = summary.data?.state_machine?.[current] ?? []
  const [next, setNext] = useState(allowed[0] ?? '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  if (allowed.length === 0) return <span className="text-xs text-slate-600">—</span>

  async function apply() {
    setBusy(true)
    setMsg(null)
    try {
      await api.transitionResource(resourceId, {
        status: next,
        reason: 'operator console',
      })
      setMsg({ ok: true, text: `→ ${next}` })
      summary.reload()
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
          value={next}
          onChange={(e) => setNext(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-xs text-slate-200"
        >
          {allowed.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <Button onClick={apply} disabled={busy || !next}>
          {busy ? '…' : 'Set'}
        </Button>
      </div>
      {msg && (
        <span className={`text-[10px] ${msg.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
          {msg.text}
        </span>
      )}
    </div>
  )
}

export function Shelters() {
  const { data, error, loading, reload } = useApi(() => api.shelters())

  if (error) return <ErrorNote message={error} onRetry={reload} />
  if (loading || !data) return <Loading />

  return (
    <div className="space-y-4">
      <Panel title={`Shelters (${data.total})`}>
        <ul className="space-y-3">
          {data.items.map((s) => (
            <li key={s.shelter_id} className="rounded border border-slate-800 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="font-medium text-slate-200">{s.name}</div>
                  <div className="text-xs text-slate-500">
                    {s.location} · {s.operational_status}
                    {s.medical_support && ' · medical support'}
                    {s.is_accessible ? ' · accessible' : ' · access restricted'}
                  </div>
                </div>
                <div className="text-right text-xs text-slate-400">
                  <div>{s.shelter_id}</div>
                  <div>{s.available_capacity} beds free</div>
                </div>
              </div>
              <div className="mt-2">
                <CapacityBar used={s.current_occupancy} total={s.capacity} />
              </div>
              <OccupancyControl shelterId={s.shelter_id} />
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  )
}

function OccupancyControl({ shelterId }: { shelterId: string }) {
  const [change, setChange] = useState(10)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function apply(sign: number) {
    setBusy(true)
    setMsg(null)
    try {
      await api.shelterOccupancy(shelterId, sign * change, 'operator console')
      setMsg({ ok: true, text: 'recorded' })
      window.dispatchEvent(new Event('shelter-changed'))
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-2 flex items-center gap-2">
      <input
        type="number"
        value={change}
        onChange={(e) => setChange(Number(e.target.value))}
        className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
      />
      <Button onClick={() => apply(1)} disabled={busy}>
        Admit
      </Button>
      <Button onClick={() => apply(-1)} disabled={busy}>
        Discharge
      </Button>
      {msg && (
        <span className={`text-[11px] ${msg.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
          {msg.text}
        </span>
      )}
    </div>
  )
}