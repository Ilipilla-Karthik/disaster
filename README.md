# Disaster Response & Emergency Resource Coordination System

Agentic decision-support platform for emergency incident intake, severity
assessment, resource allocation and response coordination.

## What this is, and what it is not

This system **recommends**. It does not dispatch.

Every operational action requires a named, authorised human to approve the plan
and then start each individual action. Those two steps are deliberately separate:
approving a plan reserves units, and starting an action is what actually deploys
one. Both steps are written to an audit trail against the officer's name.

Specifically, the platform will not: dispatch resources on request, approve its
own plans, order evacuations, perform medical triage, invent facts, resources or
routes, or treat an unverified report as a confirmed one.

## Architecture

```
intake -> weather -> geospatial -> assessment -> resource -> allocation -> coordination -> reviewer
```

Eight agents, orchestrated with LangGraph. The reviewer is a hard safety gate: a
plan that fails it cannot be approved. Scoring, capacity and allocation are
deterministic arithmetic over verified data — no LLM participates in any
decision that affects resource movement. An LLM is optional and used only for
extraction and narration.

### Provenance is a first-class concept

Every fact carries one of four states, and the UI colours them consistently:

| State | Meaning |
| --- | --- |
| `known` | Confirmed by an authoritative source |
| `inferred` | Derived from other data; treated as a precaution |
| `unverified` | Reported but not confirmed |
| `unknown` | No data exists — **never** treated as "open" or "clear" |

An incident with unresolved fields cannot receive a confident severity; it is
reported as `verification_required` instead.

## Stack

- **Backend**: FastAPI, SQLAlchemy (async), PostgreSQL (SQLite fallback for
  local demo), LangGraph, Pydantic
- **Frontend**: React 19, TypeScript, Vite, Tailwind 4, Leaflet
- **Auth**: prototype header-based identity (`X-User`, `X-Role`) — see
  [Security notes](#security-notes)

## Running locally

### Backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # optional; runs with zero credentials
uvicorn main:app --reload
```

API on `http://localhost:8000`, interactive docs at `/docs`.

With no `DATABASE_URL` reachable the app falls back to SQLite and logs a
warning, so a fresh clone runs without provisioning a database. It is a demo
convenience — **not** production storage.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Console on `http://localhost:5173`, proxying `/api` to the backend.

## Tests

```bash
cd backend
python -m pytest tests/ -q
```

Covers TC-01 through TC-06: intake and verification, duplicate detection,
severity banding, resource allocation, the approval gate, and the resource state
machine.

## Deployment

- Backend → Render (see `render.yaml`)
- Frontend → Vercel (see `frontend/vercel.json`)

### Dependency policy

`requirements.txt` is intentionally minimal, and `.python-version` pins **3.11**.
Both matter on Render: the platform defaults to Python 3.14, where several
transitive dependencies publish no wheel, and `pip install` then fails with
`No matching distribution found` — an error invisible unless you read the build
log.

Notable exclusions, each verified as genuinely unimported rather than guessed:

- **weasyhtml** — nothing imports it. PDF output uses `reportlab`, which is a
  pure-Python wheel and needs no system libraries.
- **geopandas / shapely / pyproj** — geospatial work runs on plain lat/lon
  maths, so no projection stack is needed.
- **pandas / numpy** — no dataframes anywhere.
- **LLM SDKs** — `app/tools/llm_tool.py` imports `openai` / `google-genai` /
  `anthropic` lazily, only when `LLM_PROVIDER` selects them. The base install
  needs none of them. Install `requirements-llm.txt` to enable a provider.

Because the lazy imports are conditional, enabling a provider without also
installing `requirements-llm.txt` raises `ImportError` at call time rather than
at boot. Add both together.

## Security notes

This is a prototype and the authentication layer is **not** production grade.
Identity is asserted via request headers, which any client can set. Before any
real deployment, replace `X-User` / `X-Role` with verified auth (JWT or session
issued by a real identity provider) and set `SECRET_KEY` from a secret manager.
The human-approval gates in `PlanService` depend entirely on that identity being
genuine.

The SQLite fallback should be disabled (`DB_FALLBACK_TO_SQLITE=false`) in
production so a database outage fails loudly instead of silently degrading to
local files.