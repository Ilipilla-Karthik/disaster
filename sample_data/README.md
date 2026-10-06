# Emergency Simulation Dataset

Synthetic data for demonstration and testing. No real incident, location,
resource or person is represented. All coordinates are approximate points in
Karnataka, India, chosen so the map shows a plausible multi-district response
footprint rather than a single pin.

These files are **supplements**, not the runtime source of truth. The backend
seeds its own baseline in `backend/app/db/seed.py`; this directory exists so the
dataset is inspectable and reviewable without starting the application.

## Files

| File | Contents | Requirement |
| --- | --- | --- |
| `sources.json` | Raw reports from 5 source types, unstructured text preserved | Req 2 |
| `incidents.json` | Normalized incidents, multi-type, multi-district | Req 1, 4 |
| `resources.csv` | 26 units: 5 boats, 4 teams, 6 ambulances, 8 vehicles, 3 shelter units | Req 6 |
| `shelters.csv` | 5 shelters with deterministic capacity arithmetic | Req 7 |
| `roads.csv` | 10 routes with `fact_status` distinguishing known closure from inference | Req 9 |
| `weather_timeline.json` | 6-row worsening timeline for the flood scenario | Req 9, TC-07 |

## Coverage against the brief

- **Multiple incident types** — flood, earthquake, wildfire, landslide,
  industrial accident.
- **Multiple geographic locations** — Example District zones A/B/C plus Mysuru,
  Mangaluru, Hassan and Hubballi.
- **Multiple sources** — emergency call, field team, citizen report,
  government alert, IoT sensor, gauge network.
- **Road conditions** — open, closed, partially blocked, degraded, unknown.
- **Weather changes** — escalating rainfall and wind, then partial improvement.
- **Resource shortage** — see below.
- **Changing severity** — `expected_severity` per incident, plus the
  `weather_timeline` escalation that must move INC-003 upward.
- **Duplicate pair** — `INC-002` and `DUP-002`, 26 minutes apart, different
  sources, near-identical event.

## The intentional data traps

These exist to prove the system does not invent certainty. Each one is a case
where a naive implementation gives the wrong answer.

1. **`people_reported_affected: null`** on INC-004, INC-005, INC-006 and
   INC-007. Null means *not reported*, never zero. These must resolve to
   `verification_required`, not `low` and not `moderate`.
2. **SRC-006 explicitly says "did not see any vehicles... count unknown."**
   Preserving that negative observation as *unknown* is the point.
3. **`fact_status: unknown` on RD-004, RD-005 and RD-008.** Absence of a
   closure report is not evidence of access. The seed file likewise starts all
   routes unknown for this reason.
4. **RD-007 is `fact_status: inferred`.** The geospatial agent judged the road
   degraded; nobody confirmed it. It must render differently from RD-009, which
   is a confirmed closure.
5. **SRC-005 is a sensor.** Its wind readings are measurements, but the fire's
   location is not human-confirmed, so INC-005 stays `unverified`.
6. **INC-007's facility was evacuated by its own operator.** The system must not
   claim to have ordered it.
7. **DUP-002 must be linked, never auto-merged.** Requirement 3.

## Reproducing the shortage scenario (Req 11)

INC-003 (Zone C, 75 people, access road closed) requires 8 water-rescue-capable
units. The registry holds 5 rescue boats. Expected result:

```
required: 8   available: 5   deficit: 3 boats
```

The correct behaviour is to report the deficit of 3 and escalate. Inventing
helicopters, extra boats or an alternative road would be a safety failure, not
a feature. Note that RD-006 (Zone C Causeway, water) is reported open, so the
deficit is a capacity problem rather than an access problem — those are
different findings and must be reported separately.

## Test scenario mapping

| TC | Scenario | Data used |
| --- | --- | --- |
| TC-01 | New flood incident reported | `SRC-001` → `INC-001` |
| TC-02 | Same incident from two sources | `SRC-002` + `SRC-003` → `INC-002` / `DUP-002` |
| TC-03 | Critical incident with available resources | `INC-003`, boats available |
| TC-04 | Requirement exceeds availability | `INC-003` needs 8, only 5 boats |
| TC-05 | Assigned road becomes blocked | `RD-002` open at 09:06, `RD-003` closed at 09:50 |
| TC-06 | Shelter approaches capacity | `SH-A` at 160/200 (80%) |
| TC-07 | Weather forecast worsens | `weather_timeline` 06:00 → 11:00 |
| TC-08 | Same resource for two incidents | `BOAT-001` against `INC-001` and `INC-002` |

TC-05, TC-07 and TC-08 are specified here and supported by this data, but are
**not yet covered by automated tests**. The README states this explicitly rather
than implying full coverage.

## Loading

The baseline seed runs automatically on first boot (`AUTO_SEED=true`). To load
this dataset into a running instance, post the normalized incidents through the
public API so they enter the pipeline with correct provenance rather than being
inserted directly:

```
POST /api/v1/incidents
```

Direct database insertion would bypass verification status, agent assessment and
the audit trail, which are the parts this dataset is meant to exercise.