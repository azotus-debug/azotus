# Inventory And Risk Matrix

Generated: 2026-02-24 17:02:11 EST  
Source branch: `main`  
Source commit: `abf88b8`

## Working Tree Drift Snapshot
- Total changed/untracked entries: `206`
- Top-level distribution:
  - `scripts`: 84
  - `omega-frontend`: 49
  - `workers`: 10
  - `docs`: 9
  - `tests`: 8
  - `cloud`: 3
  - Root-level runtime files mixed into drift: yes

## Runtime-Critical Surface
Files/services that can directly break operation:
- [start_omega.sh](/Users/haukurhauksson/Azotus/start_omega.sh)
- [stop_all.sh](/Users/haukurhauksson/Azotus/stop_all.sh)
- [ecosystem.config.js](/Users/haukurhauksson/Azotus/ecosystem.config.js)
- [api_main.py](/Users/haukurhauksson/Azotus/api_main.py)
- [omega_manager.py](/Users/haukurhauksson/Azotus/omega_manager.py)
- [db.py](/Users/haukurhauksson/Azotus/db.py)
- [config.py](/Users/haukurhauksson/Azotus/config.py)
- [omega-frontend/next.config.ts](/Users/haukurhauksson/Azotus/omega-frontend/next.config.ts)
- [omega-frontend/.env.local](/Users/haukurhauksson/Azotus/omega-frontend/.env.local)
- [omega-frontend/src/hooks/useProgramsQuery.ts](/Users/haukurhauksson/Azotus/omega-frontend/src/hooks/useProgramsQuery.ts)

## Live Runtime Snapshot (Freeze Start)
- API health: 200 (`/api/health`)
- Stuck health: 200 (`/api/v2/health/stuck`)
- Programs endpoint: 200, non-empty (`21` programs)
- Frontend root: 200, CSS/JS assets present in HTML
- Ports listening: `3000`, `8001`, `5432`

## Risk Matrix
| Risk | Severity | Likelihood | Why It Matters | Mitigation | Gate |
|---|---|---|---|---|---|
| Unbounded repo drift on `main` | Critical | High | No reliable release baseline | Split runtime-critical vs non-critical drift; cut stabilization branch | G1 |
| Startup mode ambiguity (frontend standalone/start/dev paths) | Critical | Medium | Can produce unstyled or broken UI | Enforce one startup contract + fail-fast checks | G2, G3 |
| Env override ambiguity (`NEXT_PUBLIC_API_URL`, ports, proxy paths) | High | High | UI can show empty state while backend has data | Strict env contract + smoke gate | G2, G3 |
| Historical log noise hiding active faults | High | High | Slows incident response and creates false alarms | Classify benign warnings vs active failures; alert policy | G5 |
| Data safety not validated by restore drill | Critical | Medium | Unknown recovery capability during incident | Backup + restore test with parity checks | G4 |
| Cross-cutting refactors without release gates | High | High | Recurring regressions after restarts | Mandatory smoke + QA gate before rollout | G6 |

## Freeze Backlog (First 72 Hours)
1. Establish deterministic startup path and remove fallback ambiguity.
2. Add smoke gate script and require pass before declaring stack healthy.
3. Capture baseline artifact package for every restart cycle.
4. Partition and label drift into:
   - Runtime-critical now
   - Defer until after stabilization
   - Safe cleanup batch
5. Produce restore drill plan and dry-run checklist.

## Evidence Checklist For External Review
- `docs/stabilization/00-freeze-charter.md`
- `docs/stabilization/owners-template.md`
- `docs/stabilization/01-inventory-and-risk-matrix.md`
- Baseline artifact bundle from `scripts/stabilization/capture_baseline.sh`
- Smoke gate report from `scripts/stabilization/smoke_gate.sh`

