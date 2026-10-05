import { useState } from 'react'
import { api, ApiError } from '../api'
import { Panel, ErrorNote, Loading, Empty, Button, SafetyBanner, useApi } from '../components/ui'
import { StatusBadge } from '../components/Badges'
import type { DecisionResult, PlanGeneration } from '../types'

export default function Plans() {
  const plans = useApi(() => api.plans())
  const approvals = useApi(() => api.approvals())
  const actions = useApi(() => api.actionBoard())
  const incidents = useApi(() => api.incidents({ limit: 50 }))

  return (
    <div className="space-y-4">
      <SafetyBanner />
      <GeneratePanel incidents={incidents.data?.items ?? []} onDone={() => {
        plans.reload()
        approvals.reload()
        actions.reload()
      }} />

      <PendingApprovals
        approvals={approvals.data?.items ?? []}
        onDone={() => {
          approvals.reload()
          plans.reload()
          actions.reload()
        }}
      />

      <Panel title={`Plans (${plans.data?.total ?? 0})`}>
        {plans.error && <ErrorNote message={plans.error} onRetry={plans.reload} />}
        {plans.loading ? (
          <Loading />
        ) : (plans.data?.items.length ?? 0) === 0 ? (
          <Empty>No plans generated yet.</Empty>
        ) : (
          <ul className="divide-y divide-slate-800">
            {plans.data?.items.map((p) => (
              <li key={p.plan_id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-slate-400">{p.plan_id}</span>
                    <StatusBadge status={p.status} />
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {p.incident_ids?.join(', ')} · {p.total_affected} people affected ·{' '}
                    {new Date(p.created_at).toLocaleString()}
                  </div>
                  {p.approved_by && (
                    <div className="mt-0.5 text-xs text-emerald-300">
                      approved by {p.approved_by}
                    </div>
                  )}
                </div>
                <div className="flex gap-2">
                  {p.status !== 'approved' && p.status !== 'active' && (
                    <ApproveButton planId={p.plan_id} />
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title={`Action board (${actions.data?.total ?? 0})`}
        action={
          <span className="text-[11px] text-slate-500">
            starting an action is what activates a plan and deploys the unit
          </span>
        }
      >
        {actions.error && <ErrorNote message={actions.error} onRetry={actions.reload} />}
        {actions.loading ? (
          <Loading />
        ) : (actions.data?.items.length ?? 0) === 0 ? (
          <Empty>No actions yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">Action</th>
                  <th className="py-2 pr-3">Plan</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Assigned</th>
                  <th className="py-2">Control</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {actions.data?.items.map((a) => (
                  <tr key={a.action_id}>
                    <td className="py-2.5 pr-3">
                      <div className="text-slate-200">{a.action_type.replace(/_/g, ' ')}</div>
                      <div className="text-xs text-slate-500">{a.description}</div>
                      {a.resource_id && (
                        <div className="font-mono text-[10px] text-sky-400">
                          {a.resource_id}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 font-mono text-xs text-slate-500">
                      {a.plan_id}
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge status={a.status} />
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-slate-400">
                      {a.assigned_to ?? '—'}
                    </td>
                    <td className="py-2.5">
                      <ActionControl actionId={a.action_id} status={a.status} onDone={actions.reload} />
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

function ApproveButton({ planId }: { planId: string }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  return (
    <div className="text-right">
      <Button
        variant="primary"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setMsg(null)
          try {
            await api.requestApproval(planId)
            setMsg('approval requested')
            window.dispatchEvent(new Event('plan-changed'))
          } catch (e) {
            setMsg(e instanceof ApiError ? e.message : String(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        Request approval
      </Button>
      {msg && <div className="mt-1 text-[10px] text-slate-400">{msg}</div>}
    </div>
  )
}

function PendingApprovals({
  approvals,
  onDone,
}: {
  approvals: { request_id: string; plan_id: string; status: string; requested_from: string }[]
  onDone: () => void
}) {
  const pending = approvals.filter((a) => a.status === 'pending')

  if (pending.length === 0) {
    return (
      <Panel title="Awaiting your decision">
        <Empty>No plans are waiting for approval.</Empty>
      </Panel>
    )
  }

  return (
    <Panel title={`Awaiting your decision (${pending.length})`}>
      <p className="mb-3 text-xs text-slate-400">
        Approving reserves units. It does not move anything: each action is deployed separately
        below, and both steps are recorded against your name.
      </p>
      <ul className="space-y-2">
        {pending.map((p) => (
          <ApprovalRow key={p.request_id} request={p} onDone={onDone} />
        ))}
      </ul>
    </Panel>
  )
}

function ApprovalRow({
  request,
  onDone,
}: {
  request: { request_id: string; plan_id: string; status: string; requested_from: string }
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showResult, setShowResult] = useState<DecisionResult | null>(null)

  async function decide(decision: 'approved' | 'rejected') {
    setBusy(true)
    setError(null)
    try {
      const result = await api.decideApproval(
        request.request_id,
        decision,
        decision === 'rejected' ? 'Rejected from operator console.' : undefined,
      )
      setShowResult(result)
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded border border-slate-800 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="font-mono text-xs text-slate-400">{request.plan_id}</span>
          <span className="ml-2 text-xs text-slate-500">
            requested from {request.requested_from}
          </span>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => decide('rejected')} disabled={busy} variant="danger">
            Reject
          </Button>
          <Button onClick={() => decide('approved')} disabled={busy} variant="primary">
            Approve
          </Button>
        </div>
      </div>
      {error && (
        <div className="mt-2">
          <ErrorNote message={error} />
        </div>
      )}
      {showResult && (
        <div className="mt-2 rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-xs text-emerald-200">
          Plan is now <strong>{showResult.plan_status}</strong>, decided by{' '}
          <strong>{showResult.decided_by}</strong>. Units reserved:{' '}
          {showResult.reserved_resources?.length ? showResult.reserved_resources.join(', ') : 'none'}.
          <div className="mt-1 text-emerald-300/80">{showResult.note}</div>
        </div>
      )}
    </li>
  )
}

function GeneratePanel({
  incidents,
  onDone,
}: {
  incidents: { incident_id: string; location: string }[]
  onDone: () => void
}) {
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PlanGeneration | null>(null)

  async function generate() {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const gen = await api.generatePlan(selected)
      setResult(gen)
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Generate a response plan">
      <p className="mb-3 text-xs text-slate-400">
        Generation is read-only: nothing is reserved and nothing is deployed. The result is a
        recommendation set that requires your approval.
      </p>
      <div className="flex flex-wrap gap-2">
        {incidents.length === 0 ? (
          <span className="text-xs text-slate-500">No incidents to plan for.</span>
        ) : (
          incidents.map((i) => {
            const on = selected.includes(i.incident_id)
            return (
              <button
                key={i.incident_id}
                onClick={() =>
                  setSelected((s) =>
                    on ? s.filter((x) => x !== i.incident_id) : [...s, i.incident_id],
                  )
                }
                className={`rounded border px-2 py-1 text-xs ${
                  on
                    ? 'border-sky-500 bg-sky-500/20 text-sky-200'
                    : 'border-slate-700 text-slate-300 hover:border-slate-500'
                }`}
              >
                {i.incident_id} · {i.location}
              </button>
            )
          })
        )}
      </div>
      <div className="mt-3">
        <Button variant="primary" onClick={generate} disabled={busy || selected.length === 0}>
          {busy ? 'Running 8 agents…' : `Generate plan for ${selected.length} incident(s)`}
        </Button>
      </div>

      {error && (
        <div className="mt-3">
          <ErrorNote message={error} />
        </div>
      )}

      {result && <PlanResult gen={result} />}
    </Panel>
  )
}

function PlanResult({ gen }: { gen: PlanGeneration }) {
  return (
    <div className="mt-4 space-y-3">
      <div className="rounded border border-slate-800 bg-slate-950 p-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-slate-400">{gen.plan.plan_id}</span>
          <StatusBadge status={gen.plan.status} />
          <span className="text-xs text-slate-500">review: {gen.review_status}</span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-sky-200">{gen.disclaimer}</p>
        {gen.unresolved_issues.length > 0 && (
          <div className="mt-2">
            <div className="text-xs font-semibold text-orange-300">Unresolved</div>
            <ul className="mt-1 space-y-0.5 text-[11px] text-slate-400">
              {gen.unresolved_issues.map((u, i) => (
                <li key={i}>• {u}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded border border-slate-800 p-3">
          <div className="text-xs font-semibold text-slate-200">
            Recommended allocations ({gen.allocations.length})
          </div>
          <ul className="mt-2 space-y-2">
            {gen.allocations.map((a) => (
              <li key={`${a.incident_id}-${a.resource_id}`} className="rounded bg-slate-950 p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-sky-300">{a.resource_id}</span>
                  <span className="text-[10px] text-slate-500">
                    +{a.capacity_contribution} capacity
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{a.rationale}</p>
                <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                  <span
                    className={`rounded px-1.5 py-0.5 ${
                      a.route_verified
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : 'bg-orange-500/15 text-orange-300'
                    }`}
                    title={a.route_verified ? 'Route verified' : 'Route not verified'}
                  >
                    route: {a.route_status}
                    {a.route_verified ? ' (verified)' : ' (unverified)'}
                  </span>
                  {a.distance_km != null && (
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">
                      {a.distance_km.toFixed(1)} km
                    </span>
                  )}
                  {a.travel_minutes != null && (
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">
                      {a.travel_minutes} min
                    </span>
                  )}
                </div>
                {a.constraints.map((c, i) => (
                  <p key={i} className="mt-1 text-[10px] leading-relaxed text-slate-500">
                    ! {c}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded border border-rose-500/25 bg-rose-500/5 p-3">
          <div className="text-xs font-semibold text-rose-200">
            Shortfalls ({gen.shortages.length})
          </div>
          {gen.shortages.length === 0 ? (
            <p className="mt-2 text-[11px] text-slate-400">
              Recommended allocations cover the reported need.
            </p>
          ) : (
            <ul className="mt-2 space-y-2">
              {gen.shortages.map((s, i) => (
                <li key={i} className="rounded bg-slate-950 p-2 text-[11px]">
                  <div className="text-slate-200">{s.label}</div>
                  <div className="mt-1 text-rose-300">
                    required {s.required} · available {s.available} · uncovered{' '}
                    {s.required - s.available}
                  </div>
                  <p className="mt-1 text-slate-500">
                    Escalation or mutual aid is required. This gap is not covered by the plan.
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}

function ActionControl({
  actionId,
  status,
  onDone,
}: {
  actionId: string
  status: string
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function call(path: 'start' | 'complete') {
    setBusy(true)
    setError(null)
    try {
      await (path === 'start'
        ? api.startAction(actionId, 'chief.morales')
        : api.completeAction(actionId, 'chief.morales'))
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (status === 'completed' || status === 'cancelled') {
    return <span className="text-xs text-slate-600">—</span>
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        variant={status === 'in_progress' ? 'default' : 'primary'}
        disabled={busy}
        onClick={() => call(status === 'in_progress' ? 'complete' : 'start')}
      >
        {status === 'in_progress' ? 'Complete' : 'Start'}
      </Button>
      {error && (
        <span className="max-w-[16rem] text-[10px] leading-tight text-rose-300">{error}</span>
      )}
    </div>
  )
}