import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, Link } from 'react-router-dom'
import { api, getIdentity, setIdentity } from './api'
import { ErrorNote } from './components/ui'
import type { Health } from './types'
import Dashboard from './pages/Dashboard'
import Incidents from './pages/Incidents'
import Resources, { Shelters } from './pages/Resources'
import Roads from './pages/Roads'
import Plans from './pages/Plans'
import Alerts, { Assistant, Reports } from './pages/Alerts'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/incidents', label: 'Incidents' },
  { to: '/plans', label: 'Plans' },
  { to: '/resources', label: 'Resources' },
  { to: '/shelters', label: 'Shelters' },
  { to: '/roads', label: 'Roads' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/assistant', label: 'Assistant' },
  { to: '/reports', label: 'Reports' },
]

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(String(e)))
  }, [])

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 border-r border-slate-800 bg-slate-950 p-4 md:block">
        <div className="mb-6">
          <div className="text-sm font-bold leading-tight text-slate-100">
            Disaster Response
          </div>
          <div className="text-[11px] text-slate-500">Emergency coordination console</div>
        </div>
        <nav className="space-y-0.5">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `block rounded px-2.5 py-1.5 text-sm ${
                  isActive
                    ? 'bg-sky-500/15 font-medium text-sky-200'
                    : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <IdentityBox />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-950/80 px-4 py-2.5">
          <div className="flex items-center gap-2 text-xs">
            {healthError ? (
              <span className="rounded bg-rose-500/15 px-2 py-1 text-rose-300">
                backend unreachable
              </span>
            ) : health ? (
              <>
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
                <span className="text-slate-400">
                  v{health.version} ·{' '}
                  {health.database.fallback_used ? 'SQLite (local fallback)' : 'PostgreSQL'}
                  {health.database.fallback_used && (
                    <span
                      className="ml-1 text-amber-300"
                      title={`Requested: ${health.database.requested}`}
                    >
                      · not production storage
                    </span>
                  )}
                </span>
              </>
            ) : (
              <span className="text-slate-500">connecting…</span>
            )}
          </div>
          <Link to="/assistant" className="text-xs text-sky-400 hover:underline md:hidden">
            Assistant
          </Link>
        </header>

        <main className="min-w-0 flex-1 space-y-4 p-4">
          {healthError && (
            <ErrorNote message={`Cannot reach the API: ${healthError}. Is the backend running?`} />
          )}
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/plans" element={<Plans />} />
            <Route path="/resources" element={<Resources />} />
            <Route path="/shelters" element={<Shelters />} />
            <Route path="/roads" element={<Roads />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/assistant" element={<Assistant />} />
            <Route path="/reports" element={<Reports />} />
            <Route
              path="*"
              element={
                <div className="rounded border border-slate-800 p-6 text-sm text-slate-400">
                  Page not found.
                </div>
              }
            />
          </Routes>
        </main>

        <footer className="border-t border-slate-800 px-4 py-2 text-[11px] text-slate-500">
          Decision-support only. Plans are recommendations; deployment requires an authorised
          commander to approve and start each action.
        </footer>
      </div>
    </div>
  )
}

/**
 * Identity is explicit and visible. Every approval and deployment is written to
 * the audit trail under this name, so it is never left implicit.
 */
function IdentityBox() {
  const [identity, setIdentityState] = useState(getIdentity())
  const [user, setUser] = useState(identity.user)
  const [role, setRole] = useState(identity.role)

  return (
    <div className="mt-6 border-t border-slate-800 pt-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Acting as
      </div>
      <input
        value={user}
        onChange={(e) => setUser(e.target.value)}
        className="mt-1.5 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
      />
      <select
        value={role}
        onChange={(e) => {
          const r = e.target.value
          setRole(r)
          setIdentity(user, r)
          setIdentityState({ user, role: r })
        }}
        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
      >
        <option value="commander">commander</option>
        <option value="coordinator">coordinator</option>
        <option value="observer">observer</option>
      </select>
      <button
        onClick={() => {
          setIdentity(user, role)
          setIdentityState({ user, role })
          window.location.reload()
        }}
        className="mt-1.5 w-full rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-sky-500 hover:text-sky-200"
      >
        Apply identity
      </button>
      <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
        Recorded against every approval and deployment. Observer cannot approve or deploy.
      </p>
    </div>
  )
}