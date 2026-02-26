# Stabilization Freeze Charter

Status: Active  
Start date: 2026-02-24  
Prepared for external developer review.

## Mission
Stabilize Omega operations before any new feature work.  
Primary objective: deterministic startup, deterministic data visibility, and release confidence.

## Current Baseline
- Repo HEAD at freeze start: `abf88b8`.
- Working tree drift at freeze start: `206` changed/untracked entries.
- Runtime services expected:
  - Frontend: `127.0.0.1:3000`
  - FastAPI: `127.0.0.1:8001`
  - Cloud SQL proxy: `127.0.0.1:5432`
- Immediate health snapshot at freeze start:
  - `/api/health` -> 200
  - `/api/v2/health/stuck` -> 200
  - `/api/v2/programs` -> 200 (non-empty)

## Freeze Scope
Allowed:
- Reliability, startup, deploy, observability, test, and documentation changes.
- Data safety and rollback improvements.
- Removing ambiguity in runtime configuration.

Blocked:
- New product features.
- UI behavior changes unrelated to reliability.
- Schema changes without explicit rollback drill.

## Non-Negotiable Rules
1. No direct edits on `main` for feature work.
2. Every change must include evidence (command outputs or test logs).
3. No restart/redeploy without smoke gate.
4. Rollback steps must exist before merge.
5. If a gate fails, halt and revert to last known-good.

## Workstream Roles
- Release control: Tech Lead
- Runtime determinism: Backend Lead
- Frontend reliability: Frontend Lead
- Data safety: Data Lead
- Observability: SRE/Platform
- QA gates: QA Lead
- Security/env contract: Platform Lead
- Documentation and handoff: Engineering Manager

See `docs/stabilization/owners-template.md` for named assignments.

## Gates
| Gate | Goal | Pass Criteria | Required Evidence |
|---|---|---|---|
| G0 | Freeze active | Scope lock communicated and acknowledged | Charter approval note |
| G1 | Baseline captured | Snapshot artifact generated from running stack | `docs/stabilization/artifacts/<ts>/` |
| G2 | Runtime contract | Startup is deterministic across 10 restarts | Restart run log + smoke gate passes |
| G3 | Frontend/API contract | UI assets and programs data consistently load | Smoke gate report (frontend + API) |
| G4 | Data safety | Backup and restore drill completed | Restore report + count parity check |
| G5 | Observability | Known warnings separated from active faults | Alert policy + clean dashboard criteria |
| G6 | QA release gate | Smoke + regression suite pass on release candidate | Test report + sign-off |

## Required Commands During Freeze
- Start stack: `OMEGA_NO_TAIL=1 ./start_omega.sh`
- Stop stack: `./stop_all.sh`
- Capture baseline: `./scripts/stabilization/capture_baseline.sh`
- Run smoke gate: `./scripts/stabilization/smoke_gate.sh`

## Exit Criteria
Freeze ends only when G0-G6 are all passed and signed by Tech Lead + Product Owner.

