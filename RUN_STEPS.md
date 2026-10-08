# How to run this project from a fresh ZIP

Two processes are required: a Python backend and a React frontend. They run on
different ports and talk to each other over HTTP.

---

## 0. Prerequisites

| Tool | Version | Check |
| --- | --- | --- |
| Python | 3.11 – 3.13 | `python --version` |
| Node.js | 20 or 22 | `node --version` |
| npm | 9+ | `npm --version` |
| PostgreSQL | optional | `psql --version` |

**You do not need PostgreSQL to run this.** With no reachable database the
backend falls back to SQLite automatically and logs a warning, so the whole
system demonstrates locally with zero external services and zero API keys.

**You do not need an LLM API key.** `LLM_PROVIDER` defaults to `none`; every
agent falls back to deterministic logic. The system runs identically without
OpenAI, Gemini or Anthropic.

---

## 1. Unzip

```powershell
Expand-Archive -Path Disaster-Response-System.zip -DestinationPath D:\work
cd D:\work\Disaster-Response-System
```

Folder layout you should see:

```
Disaster-Response-System/
├── backend/          FastAPI + LangGraph agents
├── frontend/         React + TypeScript operator console
├── sample_data/      Synthetic dataset as JSON/CSV
├── render.yaml       Deploy config for Render
├── .python-version   Pinned to 3.11
├── README.md         Project overview and architecture
├── RUN_STEPS.md      This file
├── SYSTEM_GUIDE.md   Routes, connections and demo walkthrough
└── .gitignore
```

---

## 2. Start the backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate        # PowerShell
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
uvicorn main:app --reload
```

The console prints the startup sequence. Wait for:

```
Startup complete: database=... fallback=True
```

| | |
| --- | --- |
| Base URL | http://127.0.0.1:8000 |
| Interactive docs | http://127.0.0.1:8000/docs |
| Health check | http://127.0.0.1:8000/health |
| Safety policy | http://127.0.0.1:8000/safety/policy |

Confirm it is up:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

First boot creates tables and seeds the dataset: 26 resources, 3 shelters,
6 roads, 0 incidents. Incidents are created through the app, not the seed,
because they must pass through the agent pipeline with provenance attached.

---

## 3. Start the frontend

Open a **second** terminal:

```powershell
cd frontend
npm install
npm run dev
```

| | |
| --- | --- |
| Console | http://localhost:5173 |
| API proxy | `/api`, `/health`, `/safety` → `http://127.0.0.1:8000` |

The Vite dev server proxies API calls to the backend, so the browser stays
same-origin. **No CORS configuration is involved during local development.**

Verify both layers:

```powershell
Invoke-RestMethod http://localhost:5173/health     # proxied -> backend
Invoke-RestMethod http://127.0.0.1:8000/health     # direct
```

Both return the same JSON. If the first fails but the second works, the
frontend is running but the backend is not.

---

## 4. Run the tests

In a third terminal:

```powershell
cd backend
.venv\Scripts\activate
python -m pytest tests/ -q
```

Expected: **46 passed** in roughly 6 seconds. Tests use a throwaway temporary
SQLite file, not your application database.

---

## 5. Environment variables

Every variable has a working default. Nothing is required for a local demo.

Create `backend/.env` only if you want to override something:

```ini
# Database. Leave unset for SQLite fallback.
DATABASE_URL=
DB_FALLBACK_TO_SQLITE=true
AUTO_CREATE_TABLES=true
AUTO_SEED=true

# LLM. none = no key needed, deterministic fallback.
LLM_PROVIDER=none
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=

# External integrations. Empty key = documented simulation mode.
WEATHER_API_KEY=
ROUTING_API_BASE=https://router.project-osrm.org
GEOCODING_API_BASE=https://nominatim.openstreetmap.org

# Identity / security
SECRET_KEY=change-this-in-production
COORDINATOR_NAME=duty.coordinator
CORS_ORIGINS=["http://localhost:5173"]
```

Two gotchas:

- `CORS_ORIGINS` is a **JSON array**, not a comma-separated string. Pydantic
  will reject `http://localhost:5173`.
- Local dev needs **no CORS change** — the Vite proxy handles it. Only add
  origins if you call the backend cross-origin from somewhere else.

---

## 6. Optional: use PostgreSQL instead of SQLite

```powershell
psql -U postgres -c "CREATE DATABASE disaster_response;"
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/disaster_response"
uvicorn main:app --reload
```

If the connection string is wrong, the app logs a warning and falls back to
SQLite when `DB_FALLBACK_TO_SQLITE=true`. Set it to `false` to make an
unreachable database a hard failure instead.

---

## 7. Stop and restart

| Action | How |
| --- | --- |
| Stop backend | `Ctrl+C` in its terminal |
| Stop frontend | `Ctrl+C` in its terminal |
| Wipe local data | stop backend, delete `backend/disaster_response.db` |
| Reset the dataset | stop backend, delete the `.db`, restart |

The dataset seeding is idempotent: on restart it sees existing rows and skips,
so your demo data survives a reboot. Delete the database file for a clean slate.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Frontend loads, every panel errors | Backend not running | Start `uvicorn main:app --reload` |
| `Connection refused` on port 8000 | Wrong port or crashed backend | Check the backend terminal for a traceback |
| `no matching distribution` during pip | Python 3.14 | Use 3.11: `py -3.11 -m venv .venv` |
| SQLite warning at startup | PostgreSQL unreachable — expected | Ignore locally, or fix `DATABASE_URL` |
| `403` on approve/start | Missing or wrong identity headers | Send `X-User` and `X-Role: commander` |
| Port 5173 busy | Another dev server | Use the port Vite prints, e.g. 5174 |
| Blank map tiles | No internet access | Map is cosmetic; all other data still works |
| Tests bind to old data | Stale `backend/disaster_response.db` | Delete it before running pytest |

---

## 9. What to read next

| File | Contents |
| --- | --- |
| `README.md` | Architecture, safety model, provenance, deployment |
| `SYSTEM_GUIDE.md` | Every route, frontend/backend wiring, demo walkthrough |
| `sample_data/README.md` | The dataset and its deliberate data traps |
| `backend/tests/` | Test scenarios with expected and actual output |