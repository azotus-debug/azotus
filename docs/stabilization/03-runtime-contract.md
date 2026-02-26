# Runtime Contract (Phase 2)

Date: 2026-02-24

This defines the deterministic runtime contract for local Omega operation during freeze.

## Canonical Runtime
- Frontend: `127.0.0.1:3000`
- FastAPI: `127.0.0.1:8001`
- Cloud SQL Proxy: `127.0.0.1:5432`

## Frontend Execution Mode
Only one mode is allowed:
- `node .next/standalone/server.js`

Disallowed:
- `next start`
- `next dev`

Enforcement:
- [ecosystem.config.js](/Users/haukurhauksson/Azotus/ecosystem.config.js) now fails if standalone or static build artifacts are missing.

## Build Contract
Required artifacts before startup:
- `omega-frontend/.next/standalone/server.js`
- `omega-frontend/.next/static/`

If missing, startup fails with explicit error and instruction to rebuild:
- `cd omega-frontend && npm run build`

## Env Contract
File: `omega-frontend/.env.local`

Rules:
1. `NEXT_PUBLIC_API_URL` should be empty by default.
2. Non-empty `NEXT_PUBLIC_API_URL` requires `OMEGA_ALLOW_FRONTEND_API_OVERRIDE=1`.
3. Allowed override values are limited to:
   - `http://127.0.0.1:${OMEGA_FASTAPI_PORT}`
   - `http://localhost:${OMEGA_FASTAPI_PORT}`
4. `NEXT_PUBLIC_SOCKET_URL` should be empty by default.
5. Non-empty `NEXT_PUBLIC_SOCKET_URL` requires `OMEGA_ALLOW_FRONTEND_SOCKET_OVERRIDE=1`.

Enforcement point:
- [start_omega.sh](/Users/haukurhauksson/Azotus/start_omega.sh)

## Restart Gate Contract
Script:
- [scripts/stabilization/restart_gate.sh](/Users/haukurhauksson/Azotus/scripts/stabilization/restart_gate.sh)

Behavior:
1. Runs `start_omega.sh`.
2. Waits for backend readiness on both:
   - `/api/health`
   - `/api/v2/programs`
3. Runs smoke gate with retries.
4. Captures baseline artifacts.
5. Repeats for `OMEGA_RESTART_LOOPS` iterations.

## Release Gate Contract
Script:
- [scripts/stabilization/release_gate.sh](/Users/haukurhauksson/Azotus/scripts/stabilization/release_gate.sh)

Behavior:
1. Runs `start_omega.sh` preflight by default (can be skipped with `OMEGA_RELEASE_GATE_SKIP_START=1`).
2. Uses `OMEGA_RELEASE_GATE_SKIP_PREFLIGHT=1` by default so runtime gates are not blocked by cloud preflight checks.
3. Can bypass any remaining preflight hard-fail checks by setting `OMEGA_RELEASE_GATE_FORCE_START=1` (default for release gate).
4. Runs restart gate for `OMEGA_RELEASE_GATE_RESTART_LOOPS`.
5. Captures a baseline artifact.
6. Captures drift report.
7. Writes `release_gate_summary.txt` with paths to all logs and artifact directories.

## Library Integrity Contract
Script:
- [scripts/stabilization/library_integrity_gate.sh](/Users/haukurhauksson/Azotus/scripts/stabilization/library_integrity_gate.sh)

Behavior:
1. Queries active program IDs from DB (`programs` where status is not `DELETED`).
2. Fetches backend `/api/v2/programs` and frontend `/api/v2/programs` with the same limit.
3. Fails if counts are empty, counts diverge, or ID sets diverge.
4. Emits mismatch samples for fast triage.

## Verified Evidence
- Restart gate pass: `10/10`
- Artifact: `/Users/haukurhauksson/Azotus/docs/stabilization/artifacts/restart-gate-20260224T192545-0500`
