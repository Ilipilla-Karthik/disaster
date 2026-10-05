/**
 * API client.
 *
 * Two things this file is careful about:
 *
 * 1. Identity. The prototype authenticates with `X-User` / `X-Role` headers. The
 *    operator's name is sent on every mutation because it is written into the
 *    audit trail - it is not a session detail.
 * 2. Refusals. The backend rejects illegal state transitions with 409/400 and
 *    safety failures with 422. Those bodies carry a human-readable `detail`, so
 *    errors are surfaced verbatim rather than collapsed into "request failed".
 */

import type {
  ActionBoard,
  AlertPage,
  ApprovalList,
  ApprovalRequest,
  ChatAnswer,
  Dashboard,
  DecisionResult,
  Health,
  Incident,
  IncidentPage,
  IntegrationStatus,
  Plan,
  PlanGeneration,
  PlanPage,
  ResourcePage,
  ResourceSummary,
  RoadPage,
  SafetyPolicy,
  Shelter,
  SituationReport,
} from './types'

const BASE = '/api/v1'

let currentUser = 'chief.morales'
let currentRole = 'commander'

export function setIdentity(user: string, role: string) {
  currentUser = user
  currentRole = role
}

export function getIdentity() {
  return { user: currentUser, role: currentRole }
}

/** A refusal or failure, carrying whatever the backend said about it. */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

function detailFrom(payload: unknown, status: number): string {
  if (typeof payload === 'string' && payload) return payload
  if (payload && typeof payload === 'object') {
    const d = (payload as { detail?: unknown }).detail
    if (typeof d === 'string') return d
    // FastAPI validation errors arrive as a list of field problems.
    if (Array.isArray(d)) {
      return d
        .map((e) => {
          const item = e as { loc?: unknown[]; msg?: string }
          const field = (item.loc ?? []).slice(1).join('.')
          return field ? `${field}: ${item.msg}` : (item.msg ?? 'invalid')
        })
        .join('; ')
    }
  }
  return `Request failed (HTTP ${status})`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isMutation = (init.method ?? 'GET') !== 'GET'
  const res = await fetch(path.startsWith('/') ? path : `${BASE}/${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      // Identity is sent even on reads: the backend uses it to scope views.
      'X-User': currentUser,
      'X-Role': currentRole,
      ...(isMutation ? { 'X-User': currentUser } : {}),
      ...init.headers,
    },
  })

  if (res.status === 204) return undefined as T

  const text = await res.text()
  let payload: unknown = undefined
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!res.ok) throw new ApiError(res.status, detailFrom(payload, res.status))
  return payload as T
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(`${BASE}/${path}`, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const api = {
  // Health and the policy/integration endpoints live at the root, not under /api/v1.
  health: () => request<Health>('/health'),
  integrations: () => request<IntegrationStatus>('/integrations/status'),
  safetyPolicy: () => request<SafetyPolicy>('/safety/policy'),

  dashboard: () => request<Dashboard>(`${BASE}/dashboard`),

  incidents: (params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString()
    return request<IncidentPage>(`${BASE}/incidents${qs ? `?${qs}` : ''}`)
  },
  submitIncident: (body: unknown) => post<Incident>('incidents', body),
  incident: (id: string) => request<Incident>(`${BASE}/incidents/${id}`),
  incidentMap: () => request<{ features: unknown[] }>(`${BASE}/incidents/map`),

  resources: (params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString()
    return request<ResourcePage>(`${BASE}/resources${qs ? `?${qs}` : ''}`)
  },
  resourceSummary: () => request<ResourceSummary>(`${BASE}/resources/summary`),
  transitionResource: (id: string, body: unknown) =>
    post<unknown>(`resources/${id}/transition`, body),

  shelters: () => request<{ total: number; items: Shelter[] }>(`${BASE}/shelters`),
  shelterOccupancy: (id: string, change: number, reason: string) =>
    post<Shelter>(`shelters/${id}/occupancy`, { change, reason }),

  roads: () => request<RoadPage>(`${BASE}/roads`),
  roadClosures: () => request<RoadPage>(`${BASE}/roads/closures`),
  // The backend decides whether a report is authoritative; the UI never
  // asserts `fact_status` itself, it only submits the claim and the source.
  reportRoadCondition: (roadId: string, body: unknown) =>
    post<unknown>(`roads/${roadId}/condition`, body),
  planRoadImpact: (roadId: string) => post<unknown>(`roads/${roadId}/impact`, {}),

  plans: () => request<PlanPage>(`${BASE}/plans`),
  generatePlan: (incidentIds: string[]) =>
    post<PlanGeneration>('plans/generate', { incident_ids: incidentIds }),
  plan: (id: string) => request<{ plan: Plan; actions: unknown[] }>(`${BASE}/plans/${id}`),
  requestApproval: (planId: string) => post<ApprovalRequest>(`plans/${planId}/approve-request`),
  approvals: () => request<ApprovalList>(`${BASE}/plans/approvals`),
  decideApproval: (requestId: string, decision: 'approved' | 'rejected', notes?: string) =>
    post<DecisionResult>(`plans/approvals/${requestId}/decide`, { decision, notes }),
  actionBoard: () => request<ActionBoard>(`${BASE}/plans/actions/board`),
  startAction: (actionId: string, actor: string) =>
    post<unknown>(`plans/actions/${actionId}/start`, { actor }),
  completeAction: (actionId: string, actor: string, remarks?: string) =>
    post<unknown>(`plans/actions/${actionId}/complete`, { actor, remarks }),

  alerts: (activeOnly = true) =>
    request<AlertPage>(`${BASE}/alerts?active_only=${activeOnly}`),
  acknowledgeAlert: (id: string, note?: string) =>
    post<unknown>(`alerts/${id}/ack`, { note }),

  chat: (message: string, sessionId?: string) =>
    post<ChatAnswer>('chat', { message, session_id: sessionId }),
  chatSuggestions: () =>
    request<{ suggestions: string[]; note: string }>(`${BASE}/chat/suggestions`),

  reports: () => request<{ total: number; items: SituationReport[] }>(`${BASE}/reports`),
  generateReport: () => post<SituationReport>('reports', {}),
  reportPdfUrl: (id: string) => `${BASE}/reports/${id}/pdf`,
}