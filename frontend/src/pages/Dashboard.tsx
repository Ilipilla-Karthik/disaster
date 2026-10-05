import { Link } from 'react-router-dom'
import { api } from '../api'
import { Panel, Stat, ErrorNote, Loading, Empty, SafetyBanner, useApi } from '../components/ui'
import { SeverityBadge } from '../components/Badges'

export default function Dashboard() {
  const { data, error, loading, reload } = useApi(() => api.dashboard())

  if (error) return <ErrorNote message={error} onRetry={reload} />
  if (loading || !data) return <Loading rows={4} />

  const c = data.counts

  return (
    <div className="space-y-4">
      <SafetyBanner />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Incidents"
          value={c.incidents_total}
          hint={`${c.incidents_duplicate} duplicate link(s)`}
        />
        {/* The headline safety metric: how much of the picture is unconfirmed. */}
        <Stat
          label="Need verification"
          value={c.incidents_needing_verification}
          tone={c.incidents_needing_verification > 0 ? 'warn' : 'good'}
          hint="Unconfirmed reports"
        />
        <Stat
          label="Resources"
          value={`${c.resources_available}/${c.resources_total}`}
          tone="good"
          hint="available"
        />
        <Stat
          label="Deployed"
          value={c.resources_deployed}
          tone={c.resources_deployed > 0 ? 'warn' : 'default'}
          hint="by an approved action"
        />
        <Stat
          label="Shelter beds free"
          value={c.shelter_available}
          tone={c.shelter_capacity - c.shelter_occupancy === c.shelter_available ? 'good' : 'warn'}
          hint={`${c.shelter_occupancy} of ${c.shelter_capacity} occupied`}
        />
        <Stat
          label="Active alerts"
          value={c.alerts_active}
          tone={c.alerts_active > 0 ? 'warn' : 'good'}
          hint={`${c.roads_closed} road(s) closed`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Recent incidents"
          action={
            <Link to="/incidents" className="text-xs text-sky-400 hover:underline">
              View all
            </Link>
          }
        >
          {data.recent_incidents.length === 0 ? (
            <Empty>No incidents reported yet.</Empty>
          ) : (
            <ul className="divide-y divide-slate-800">
              {data.recent_incidents.map((i) => (
                <li key={i.incident_id} className="py-2.5">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-slate-400">
                          {i.incident_id}
                        </span>
                        <SeverityBadge level={i.severity_level ?? i.severity} />
                        {i.is_duplicate && (
                          <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-[10px] text-purple-300">
                            duplicate
                          </span>
                        )}
                      </div>
                      <div className="mt-1 truncate text-sm text-slate-200">
                        {i.location} · {i.incident_type.replace(/_/g, ' ')}
                      </div>
                    </div>
                    {i.priority_score != null && (
                      <div className="text-right">
                        <div className="text-lg font-semibold tabular-nums text-slate-100">
                          {i.priority_score.toFixed(0)}
                        </div>
                        <div className="text-[10px] uppercase text-slate-500">priority</div>
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="Alerts needing attention"
          action={
            <Link to="/alerts" className="text-xs text-sky-400 hover:underline">
              All alerts
            </Link>
          }
        >
          {data.active_alerts.length === 0 ? (
            <Empty>Nothing needs attention.</Empty>
          ) : (
            <ul className="space-y-2">
              {data.active_alerts.map((a) => (
                <li key={a.alert_id} className="rounded border border-slate-800 p-2.5">
                  <div className="flex items-center gap-2">
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
                      <span className="font-mono text-[10px] text-slate-500">
                        {a.incident_id}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-slate-300">{a.message}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  )
}