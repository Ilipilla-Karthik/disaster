# System Guide — routes, local wiring, and demonstration walkthrough

Companion to `RUN_STEPS.md`. This file documents every route, how the frontend
reaches the backend locally, and a step-by-step demonstration with the exact
responses you should expect.

---

## 1. Process model

```
Browser ──> Vite dev server (localhost:5173)
              │  proxy: /api, /health, /safety, /integrations
              └────────────> FastAPI (127.0.0.1:8000)
                                ├── SQLite fallback (local default)
                                ├── PostgreSQL (DATABASE_URL set)
                                └── LangGraph eight-agent pipeline
```

Locally the frontend never speaks to port 8000 directly. `vite.config.ts` proxies
`/api`, `/health` and `/safety` to `127.0.0.1:8000`, so every request is
same-origin and **CORS never applies during local development**.

In production the same shape is preserved, but Vercel performs the proxy via
`frontend/vercel.json` rewrites instead:

```json
{ "source": "/api/:path*", "destination": "https://disaster-response-api-yp3q.onrender.com/api/:path*" }
```

That is the whole backend↔frontend integration. `api.ts` uses relative paths:

```ts
const BASE = '/api/v1'
```

There is no configurable base URL and no environment variable involved. The
`server.proxy` block in `vite.config.ts` is dev-only and ignored at build time.

---

## 2. Backend

### 2.1 Stack and layout

| Concern | Technology |
| --- | --- |
| Web framework | FastAPI 0.142 |
| Database | SQLAlchemy async → PostgreSQL (`asyncpg`) or SQLite (`aiosqlite`) |
| Orchestration | LangGraph |
| Validation | Pydantic v2 + pydantic-settings |
| PDF reports | ReportLab |
| Real-time | WebSockets (alert fan-out) |
| Tests | pytest + pytest-asyncio (46 tests) |

```
backend/
├── main.py                  app factory, lifespan, meta endpoints
├── app/
│   ├── api/deps.py          identity + require_commander gate
│   ├── api/v1/              8 routers (see §3)
│   ├── agents/              the eight specialised agents
│   ├── orchestration/graph.py  LangGraph wiring, AGENT_REGISTRY
│   ├── services/            plan, resource, alert, alert_service
│   ├── db/session.py        engine, safe_url() redaction
│   ├── db/seed.py           baseline dataset
│   ├── core/config.py       all settings, all env-overridable
│   ├── core/constants.py    prohibitions, disclaimer
│   └── tools/               weather, routing, geocoding, llm, pdf
└── tests/                   46 tests: TC-01..TC-06 + security
```

### 2.2 The eight agents

Registry keys from `app/orchestration.graph.AGENT_REGISTRY`:

| Agent | Responsibility |
| --- | --- |
| `intake` | Parse reports, extract structured fields, flag missing data, flag duplicates |
| `weather` | Current + forecast conditions, warnings, escalation potential |
| `geospatial` | Geocoding, road accessibility, distance/time, proximity to resources |
| `assessment` | Deterministic severity banding, priority scoring, verification needs |
| `resource` | Resource state, capability matching, availability |
| `allocation` | Capability/distance/accessibility matching, shortage detection |
| `coordination` | Consolidate into one plan, recommended actions |
| `reviewer` | Hard gate — rejects unsupported assumptions and duplicate allocation |

All eight read and write one shared workflow state. None calls another directly;
there is no side-channel communication, so a full trace of any recommendation is
recorded in `run_id` / `trace`.

### 2.3 Safety gates (enforced in code)

- `require_commander` in `app/api/deps.py:72` — allowed roles are `commander`,
  `officer`, `admin`, `communications`. Anonymous callers get 403.
- Approving a plan **reserves** units. Starting an action **deploys** them. These
  are separate endpoints, both audit-logged against the named actor.
- `PlanService` rejects approval of a plan the reviewer rejected.
- Resource state machine refuses illegal transitions with 409.

### 2.4 Algorithms

**Duplicate detection** (`DUPLICATE_WINDOW_HOURS=6`, `DUPLICATE_RADIUS_KM=2.0`,
`DUPLICATE_SIMILARITY_THRESHOLD=0.6`): incidents are compared on type, time window,
geographic distance, and description token overlap. Matches are *linked* for human
review and never merged or deleted. Output includes `matched_on` so the operator
can see *why* it matched.

**Priority / severity**: deterministic arithmetic over verified data — affected
population, accessibility, weather warning level, escalation potential, existing
response status. An LLM never produces a severity number. When inputs are
insufficient the result is `verification_required`, not a low score.

**Allocation**: capability matching → accessibility filtering → distance ordering →
capacity aggregation. Deficits are reported explicitly; no invented resources.

**Shelter capacity**: `available_capacity = capacity - current_occupancy`,
`utilization_pct = occupancy / capacity * 100`. Pure arithmetic, no rounding drift.

---

## 3. All backend routes

41 paths / 47 operations. Prefix `/api/v1` unless shown otherwise.

### Meta (no prefix)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Service name, version, disclaimer |
| GET | `/health` | DB actually in use, reachable, fallback, WebSocket clients |
| GET | `/integrations/status` | Live vs simulated per external dependency |
| GET | `/safety/policy` | Prohibitions and human gates, served as data |

### Incidents

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/incidents` | Paginated list, filters |
| POST | `/api/v1/incidents` | Register incident → runs intake + assessment |
| GET | `/api/v1/incidents/{incident_id}` | Full detail + assessments |
| PATCH | `/api/v1/incidents/{incident_id}` | Update |
| GET | `/api/v1/incidents/{incident_id}/duplicates` | Duplicate candidates |
| GET | `/api/v1/incidents/map` | GeoJSON features for the map |
| GET | `/api/v1/map` | Combined map layers |
| GET | `/api/v1/dashboard` | Aggregated operational picture |

### Resources

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/resources` | Paginated registry |
| POST | `/api/v1/resources` | Register a unit |
| GET | `/api/v1/resources/{resource_id}` | Detail |
| PATCH | `/api/v1/resources/{resource_id}` | Update |
| GET | `/api/v1/resources/summary` | Counts by status/type |
| POST | `/api/v1/resources/{resource_id}/transition` | Legal state change |

### Shelters

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/shelters` | All shelters |
| GET | `/api/v1/shelters/{shelter_id}` | Detail |
| PATCH | `/api/v1/shelters/{shelter_id}` | Update |
| GET | `/api/v1/shelters/near-capacity` | Over the 85% threshold |
| POST | `/api/v1/shelters/{shelter_id}/occupancy` | Change occupancy (409 if overfill) |

### Roads and accessibility

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/roads` | All routes |
| GET | `/api/v1/roads/{road_id}` | Detail |
| GET | `/api/v1/roads/closures` | Known closures only |
| POST | `/api/v1/roads/route` | Routing analysis (OSRM or simulation) |
| POST | `/api/v1/roads/{road_id}/condition` | Report a condition; backend sets `fact_status` |

### Planning and human-in-the-loop

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/plans` | List plans |
| POST | `/api/v1/plans/generate` | Run the agent pipeline for `incident_ids` |
| GET | `/api/v1/plans/{plan_id}` | Plan + actions |
| POST | `/api/v1/plans/{plan_id}/approve-request` | Create approval request |
| GET | `/api/v1/plans/approvals` | Pending approvals |
| POST | `/api/v1/plans/approvals/{request_id}/decide` | Approve / reject + notes |
| GET | `/api/v1/plans/actions/board` | Action tracker (`total`, `items`) |
| POST | `/api/v1/plans/actions/{action_id}/start` | **Deploys** the unit |
| POST | `/api/v1/plans/actions/{action_id}/complete` | Finish an action |

### Alerts, reporting, chat

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/alerts` | `active_only` filter |
| POST | `/api/v1/alerts/broadcast` | Raise an alert |
| POST | `/api/v1/alerts/{alert_id}/ack` | Acknowledge |
| GET | `/api/v1/audit` | Full audit trail |
| GET | `/api/v1/reports` | List situation reports |
| POST | `/api/v1/reports` | Generate a report |
| GET | `/api/v1/reports/{report_id}` | Report content |
| GET | `/api/v1/reports/{report_id}/pdf` | PDF download |
| POST | `/api/v1/chat` | Assistant Q&A |
| GET | `/api/v1/chat/suggestions` | Suggested prompts |

### Identity headers

Every request carries `X-User` and `X-Role`. Mutations require both. Without
them you get `403 "Plan approval and dispatch require a commander or officer role."`

---

## 4. Frontend

### 4.1 Stack

React 19 · TypeScript · Vite 8 · Tailwind 4 · Leaflet (react-leaflet) ·
react-router-dom 7. Build: `npm run build` → `dist/`.

### 4.2 Routes

| Route | Page | Primary API calls |
| --- | --- | --- |
| `/` | `Dashboard` | `dashboard` |
| `/incidents` | `Incidents` | `incidents`, `submitIncident` |
| `/plans` | `Plans` | `generatePlan`, `plans`, `approvals`, `decideApproval`, `requestApproval`, `actionBoard`, `startAction`, `completeAction` |
| `/resources` | `Resources` | `resources`, `resourceSummary`, `transitionResource` |
| `/shelters` | `Shelters` (exported from `Resources`) | `shelters`, `shelterOccupancy` |
| `/roads` | `Roads` | `roads`, `reportRoadCondition` |
| `/alerts` | `Alerts` | `alerts`, `acknowledgeAlert` |
| `/assistant` | `Assistant` (exported from `Alerts`) | `chat`, `chatSuggestions` |
| `/reports` | `Reports` (exported from `Alerts`) | `reports`, `generateReport`, `reportPdfUrl` |
| `*` | not found | — |

Map rendering lives in `components/MapView.tsx`, provenance colouring in
`components/Badges.tsx`, layout primitives in `components/ui.tsx`.

Note the brief lists twelve frontend pages. This implementation uses nine routes
because several are deliberately colocated: `/plans` holds response planning,
the approval centre and the action tracker, since approval and deployment are
one continuous workflow — separating them into pages would imply they are
independent steps, which they are not.

### 4.3 Error handling

`api.ts` reads FastAPI's `detail` and surfaces it verbatim. Safety refusals read
as sentences, not generic failures:

```
Occupancy change of 45 would exceed SH-A's declared capacity of 200 (would reach
205). Escalate for additional shelter provision - the system will not silently
overfill a shelter.
```

FastAPI validation errors are flattened field-by-field rather than collapsed.

---

## 5. Demonstration walkthrough

Backend and frontend both running (see `RUN_STEPS.md`). Follow in order — later
steps depend on earlier ones.

### Step 1 — Confirm both layers

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://localhost:5173/health      # proves the proxy works
```

Expected: `status: ok`, `reachable: true`, `sqlite: true` locally.

Open **http://localhost:5173**. Dashboard renders with 0 incidents and the
seeded inventory: **26 resources, 3 shelters, 6 roads**.

### Step 2 — Register an incident (TC-01)

On `/incidents`, or via API:

```powershell
$headers = @{ "X-User"="chief.morales"; "X-Role"="commander"; "Content-Type"="application/json" }
$body = @{
  incident_type = "flood"
  location = "Zone A"
  latitude = 12.9735; longitude = 77.5920
  description = "Flood water entered 20 houses. Around 40 residents may need evacuation."
  people_reported_affected = 40
  assistance_requested = "evacuation"
  source = "emergency_call"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/incidents -Headers $headers -Body $body
```

Expected: `201`, id like `INC-001`, `verification_status: pending`.

The intake agent logs the extraction method and whether verification was
required. The incident appears on the dashboard immediately.

### Step 3 — Duplicate detection (TC-02)

Submit the same event again as a different source:

```powershell
$body = @{ incident_type="flood"; location="Zone A"; latitude=12.9737; longitude=77.5922
  description="flood water in Zone A houses, about 40 people need evacuation"
  people_reported_affected=40; source="citizen_report" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/incidents -Headers $headers -Body $body
```

Then inspect:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/incidents/<NEW_ID>/duplicates -Headers $headers
```

Expected response shape:

```json
{"incident_id":"INC-002","candidates":[{"incident_id":"INC-002","duplicate_of":"INC-001",
"similarity":0.9406,"matched_on":["incident_type","location_exact","within_0.03km",
"within_0.0h","description_similarity_0.62"],"requires_review":true}],
"policy":"Candidates are surfaced for human review only. N..."}
```

**Both incidents still exist.** Linked for review, never merged or deleted.

### Step 4 — Generate a plan

```powershell
$body = @{ incident_ids=@("INC-001") } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/plans/generate -Headers $headers -Body $body
```

Expected: `201` with `run_id`, `review_status`, `assessments`, `allocations`,
`shortages`, `alternatives`, `unresolved_issues`, `assumptions`, `trace`,
`plan`, `disclaimer`.

Observed for the incident above: **2 allocations, 1 shortage**, plus a raised
alert such as:

```
Resource shortage: Evacuation/rescue capacity for INC-001 - required 40,
available 16, deficit 24 person-places. Escalation or mutual-aid request required.
```

`trace` records which agent produced each piece — that is your explanation.

### Step 5 — Human approval (TC-03, Req 12)

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/plans/<PLAN_ID>/approve-request -Headers $headers
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/plans/approvals/<REQ_ID>/decide `
  -Headers $headers -Body (@{ decision="approved"; notes="Approved for Zone A evacuation." } | ConvertTo-Json)
```

Expected log:

```
Resource BOAT-001: available -> reserved (Approved plan PLAN-xxxx by chief.morales)
Resource BOAT-004: available -> reserved (Approved plan PLAN-xxxx by chief.morales)
Plan PLAN-xxxx APPROVED by chief.morales; 2 unit(s) reserved
```

**Nothing has deployed yet.** Approval reserves. Rejection is also supported:
`decision: "rejected"`.

**Refusal demo** — repeat Step 5 with no headers. Expected `403`:
*"Plan approval and dispatch require a commander or officer role."*

### Step 6 — Deploy (TC-03)

```powershell
$r = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/plans/actions/board -Headers $headers
$r.items | Select-Object action_id, status, priority
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/plans/actions/<ACTION_ID>/start" -Headers $headers -Body (@{actor="chief.morales"} | ConvertTo-Json)
```

Expected log: `Resource BOAT-001: reserved -> deployed (Action ACT-xxxx started by chief.morales)`

Registry afterwards:

```
{'available': 24, 'reserved': 1, 'deployed': 1}
```

26 units, one deployed, one still reserved for the second recommendation.

**This is the safety invariant worth showing:** two endpoints, two steps, both
audited. The UI cannot deploy in a single click.

### Step 7 — Shelter capacity (TC-06)

SH-A starts at **160/200 (80%)**, threshold is **85%**.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/shelters/SH-A/occupancy -Headers $headers -Body (@{change=15; reason="influx from Zone A"} | ConvertTo-Json)
```

Expected: `utilization_pct: 87.5`, `available_capacity: 25`, and an alert:

```
warning  | Shelter Shelter A (SH-A) at 87.5% capacity (175/200, 25 places left).
```

Push it to full with `change=25`:

```
critical | Shelter Shelter A (SH-A) at 100.0% capacity (200/200, 0 places left).
```

`GET /api/v1/shelters/near-capacity` now returns SH-A with
`operational_status: "full"`.

**Then show the refusal** — `change=45`:

```
HTTP 409 Occupancy change of 45 would exceed SH-A's declared capacity of 200
(would reach 205). Escalate for additional shelter provision - the system will
not silently overfill a shelter.
```

### Step 8 — Road condition and replanning (TC-05, Req 10)

Seeded routes all start `status: unknown`, `fact_status: unknown` — absence of a
closure report is not evidence of access. Report a real closure:

```powershell
$body = @{ status="closed"; reported_by="Field Team TEAM-002"
  blocked_reason="Water over carriageway"; source="field_team" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/roads/RD-002/condition -Headers $headers -Body $body
```

`status` accepts `open`, `partially_blocked`, `closed`, `impassable`, `unknown`.
`reported_by` is **required** — omitting it returns `422 Field required`.

Expected response:

```json
{"road_id":"RD-002","previous_status":"unknown","status":"closed",
 "fact_status":"known","is_fact":true,
 "plan_impact":{"replan_required":false,"affected_plans":[],
                "note":"No active plan depends on this route."}}
```

`plan_impact` **is** the dynamic-replanning hook (Req 10): with a plan already
active and its unit routed via RD-002, `replan_required` becomes `true` and the
affected plan is listed. A critical alert fires at the same time:

```
critical | RD-002 (Main Road to Zone B) reported as closed: Water over carriageway
```

Now check what the system will and will not claim:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/roads/closures -Headers $headers
```

```json
{"count":1,"items":[...],"note":"Only closures reported by an authoritative
source appear here. Agent inferences are never promoted to this list."}
```

Confirm the boundary case — an unreported route:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/roads/RD-004 -Headers $headers
```

Expected: `status: unknown`, `fact_status: unknown`, `is_fact: false`. RD-004 is
neither open nor closed. That is the known-vs-inferred distinction working.

### Step 9 — Alerts and real-time

```powershell
(Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/alerts?active_only=true" -Headers $headers).items |
  Select-Object severity, message
```

Expected at this point (4-5 alerts): plan awaiting approval, plan generated,
resource shortage, shelter capacity, duplicate linked.

Alerts are also pushed over WebSocket; the frontend subscribes so new alerts
appear without a refresh.

### Step 10 — Situation report (TC-16)

```powershell
$rep = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/reports -Headers $headers -Body '{}'
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/reports/$($rep.report_id)/pdf" -Headers $headers -OutFile situation.pdf
```

Expected: `201`, then a PDF starting with `%PDF-` (roughly 9 KB). Open
`situation.pdf` — it carries the disclaimer on the first page.

### Step 11 — Verify the safety policy is data

```powershell
Invoke-RestMethod http://127.0.0.1:8000/safety/policy
```

Returns `prohibitions`, `human_gates` and `provenance_rules` as structured JSON.
The same text drives `/safety/policy`, the reviewer agent and the frontend
disclaimer — one source, so the UI cannot drift from the enforced behaviour.

---

## 6. Environment variables

Full list, all optional (defaults in `app/core/config.py`):

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/disaster_response` | Unreachable → SQLite if fallback enabled |
| `SQLITE_URL` | `sqlite+aiosqlite:///./disaster_response.db` | |
| `DB_FALLBACK_TO_SQLITE` | `true` | Set `false` in production |
| `AUTO_CREATE_TABLES` / `AUTO_SEED` | `true` | |
| `LLM_PROVIDER` | `none` | `openai` \| `gemini` \| `anthropic` \| `none` |
| `LLM_MODEL` | `gpt-4o-mini` | |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | empty | Only needed if a provider is selected |
| `WEATHER_API_KEY` | empty | Empty → simulation mode |
| `WEATHER_API_BASE` | OpenWeatherMap | |
| `ROUTING_API_BASE` | `https://router.project-osrm.org` | Public demo, unauthenticated |
| `GEOCODING_API_BASE` | Nominatim | |
| `MAP_TILE_URL` | OSM tiles | |
| `SECRET_KEY` | `change-this-in-production` | **Must be overridden in production** |
| `COORDINATOR_NAME` | `duty.coordinator` | |
| `CORS_ORIGINS` | localhost:3000, 5173, 127.0.0.1:5173 | **JSON array**, not comma-separated |
| `SHELTER_ALERT_THRESHOLD_PCT` | `85.0` | |
| `DUPLICATE_WINDOW_HOURS` | `6` | |
| `DUPLICATE_RADIUS_KM` | `2.0` | |
| `DUPLICATE_SIMILARITY_THRESHOLD` | `0.6` | |
| `ACTION_OVERDUE_MINUTES` | `120` | |
| `FALLBACK_TRAVEL_MINUTES` | `30` | When OSRM is unreachable |

The frontend reads **no environment variables at all**.

---

## 7. Known issues

Documented deliberately rather than discovered during review:

1. **TC-05, TC-07 and TC-08 have no automated tests.** The underlying behaviour
   exists — road-condition reporting returns a `plan_impact.replan_required`
   flag, and the dataset provides a worsening-weather timeline — but there is no
   test asserting it. Coverage is 46 tests over TC-01..TC-04 and TC-06 plus the
   security suite.
2. **Identity is prototype-grade.** `X-User`/`X-Role` headers are forgeable by
   any client, and every approval gate trusts them. Acceptable for a demo;
   prerequisite-real use would need a real identity provider.
3. **`GET /shelters/near-capacity` returns `threshold_pct: null`** while the
   alert engine uses `SHELTER_ALERT_THRESHOLD_PCT` correctly. Cosmetic — the
   filtering is right, the field just is not populated.
4. **Auth is not production security.** `SECRET_KEY` still has its placeholder
   default locally.
