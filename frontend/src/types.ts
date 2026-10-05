/**
 * Backend contract types.
 *
 * These mirror the API exactly. `factStatus` in particular is not decoration:
 * the UI must be able to distinguish a confirmed road closure from a rumour,
 * so the type is a closed union rather than `string`.
 */

export type FactStatus = 'known' | 'inferred' | 'unverified' | 'unknown'

export type Severity =
  | 'low'
  | 'moderate'
  | 'high'
  | 'critical'
  | 'verification_required'

export type ResourceStatus =
  | 'available'
  | 'reserved'
  | 'deployed'
  | 'en_route'
  | 'returning'
  | 'unavailable'
  | 'maintenance'

export type PlanStatus =
  | 'draft'
  | 'awaiting_approval'
  | 'approved'
  | 'active'
  | 'rejected'
  | 'superseded'
  | 'expired'

export interface Health {
  status: string
  version: string
  database: {
    in_use: string
    requested: string
    reachable: boolean
    fallback_used: boolean
    sqlite: boolean
  }
  websocket_clients: Record<string, number>
  disclaimer: string
}

export interface IntegrationStatus {
  integrations: Integration[]
}

export interface Integration {
  name: string
  configured: boolean
  mode: string
  detail?: string
}

export interface SafetyPolicy {
  decision_support_only: boolean
  human_approval_required: string[]
  forbidden_actions: string[]
  disclaimer: string
}

export interface Incident {
  incident_id: string
  incident_type: string
  location: string
  latitude: number | null
  longitude: number | null
  description: string | null
  people_reported_affected: number
  people_displaced: number
  infrastructure_issue: string | null
  assistance_requested: string | null
  source: string | null
  source_type: string | null
  confidence: string | null
  verification_status: string
  severity?: Severity
  severity_level?: Severity
  priority_score?: number
  missing_fields: string[]
  is_duplicate: boolean
  duplicate_of: string | null
  duplicate_similarity: number | null
  linked_incident_ids: string[]
  reported_at: string
  intake?: {
    extraction_method: string | null
    missing_fields: string[]
    verification_required: boolean
    notes: string[]
  }
}

export interface IncidentPage {
  total: number
  items: Incident[]
}

export interface Resource {
  resource_id: string
  resource_type: string
  status: ResourceStatus
  capability: string | null
  capability_tags: string[]
  capacity: number | null
  location: string | null
  latitude: number | null
  longitude: number | null
  current_assignment: string | null
  expected_availability: string | null
}

export interface ResourcePage {
  total: number
  items: Resource[]
  by_status?: Record<string, number>
  by_type?: Record<string, number>
  total_capacity?: number
}

export interface Shelter {
  shelter_id: string
  name: string
  location: string
  latitude: number | null
  longitude: number | null
  capacity: number
  current_occupancy: number
  available_capacity: number
  utilization_pct: number
  operational_status: string
  is_accessible: boolean
  medical_support: boolean
}

export interface Road {
  road_id?: string
  name: string
  road_type?: string | null
  status: string
  fact_status: FactStatus
  evidence?: string | null
  source?: string | null
  passable: boolean
  latitude?: number | null
  longitude?: number | null
}

export interface RoadPage {
  total: number
  items: Road[]
}

export interface Allocation {
  incident_id: string
  resource_id: string
  resource_type: string
  resource_label: string
  rationale: string
  distance_km: number | null
  travel_minutes: number | null
  route_status: string
  route_verified: boolean
  coverage_gap: number | null
  capacity_contribution: number
  requires_commander_approval: boolean
  constraints: string[]
  match_factors?: {
    capability_fit: number
    proximity_fit: number
    availability: number
    capacity_fit: number
    access_fit: number
    route_source: string | null
    incident_access_status: string
  }
}

export interface Shortage {
  incident_id?: string
  resource_type: string
  label: string
  required: number
  available: number
  uncovered_people?: number
}

export interface Action {
  action_id: string
  plan_id: string
  incident_id: string | null
  resource_id: string | null
  action_type: string
  description: string
  priority: number
  status: 'pending' | 'approved' | 'in_progress' | 'completed' | 'cancelled'
  assigned_to: string | null
  start_time: string | null
  completion_time: string | null
  remarks: string | null
}

export interface ActionBoard {
  total: number
  items: Action[]
}

export interface Plan {
  plan_id: string
  incident_ids: string[]
  status: PlanStatus
  total_affected: number
  created_at: string
  approved_by: string | null
  approved_at: string | null
  superseded_by?: string | null
  reasoning_trace?: Record<string, unknown> | null
}

export interface PlanPage {
  total: number
  items: Plan[]
}

export interface PlanGeneration {
  run_id: string
  plan: Plan
  allocations: Allocation[]
  shortages: Shortage[]
  alternatives: unknown[]
  assessments: unknown[]
  assumptions: string[]
  unresolved_issues: string[]
  agent_errors: string[]
  review_status: string
  disclaimer: string
}

export interface ApprovalRequest {
  request_id: string
  plan_id: string
  status: 'pending' | 'approved' | 'rejected'
  requested_from: string
  requested_at?: string
  notes?: string | null
}

export interface ApprovalList {
  total: number
  items: ApprovalRequest[]
}

export interface DecisionResult {
  plan_status: PlanStatus
  decided_by: string
  reserved_resources: string[]
  note?: string
}

export interface Alert {
  alert_id: string
  alert_type: string
  severity: string
  message: string
  incident_id: string | null
  context: Record<string, unknown> | null
  is_active: boolean
  acknowledged: boolean
  acknowledged_by: string | null
  created_at: string
}

export interface AlertPage {
  total: number
  active_count: number
  items: Alert[]
}

export interface Dashboard {
  counts: {
    incidents_total: number
    incidents_duplicate: number
    incidents_needing_verification: number
    incidents_by_severity: Record<string, number>
    resources_total: number
    resources_available: number
    resources_deployed: number
    alerts_active: number
    shelter_capacity: number
    shelter_occupancy: number
    shelter_available: number
    roads_closed: number
  }
  recent_incidents: Incident[]
  active_alerts: Alert[]
}

export interface ResourceSummary {
  total: number
  available: number
  reserved: number
  deployed: number
  returning: number
  unavailable: number
  maintenance: number
  by_type: Record<string, number>
  /** Allowed next states per current state, straight from the backend. */
  state_machine: Record<string, string[]>
}

export interface ChatAnswer {
  answer: string
  citations: { label: string; detail: string }[]
  intent?: string
  disclaimer: string
}

export interface SituationReport {
  report_id: string
  title: string
  summary: string
  uncertainties: string[]
  created_at: string
}