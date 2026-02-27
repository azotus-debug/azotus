# External Developer Handoff And Rebuild Blueprint

Date: 2026-02-27  
Repository: `/Users/haukurhauksson/Azotus`  
Branch: `codex/stabilization-closeout-20260226`  
HEAD: `454fc6d7550f29221f99ebfe4579124fc28aa263`  
Working tree drift: `206` changed entries (`git status --porcelain | wc -l`)

## 1. Objective

This document gives an external engineering team enough detail to:

1. Understand exactly how the current system is built and operated.
2. Identify where and why reliability broke down.
3. Stabilize current operations during handover.
4. Rebuild the system on cleaner foundations with a controlled migration.

The goal is not another patch cycle. The goal is transfer of truth, then rebuild with fewer moving parts and stronger contracts.

## 2. Current State Snapshot (as of 2026-02-27)

Live API snapshot from local runtime:

- `GET http://127.0.0.1:8001/api/v2/health` reports:
  - `status: healthy`
  - database: `ok`
  - storage: `ok`
  - gcs: `ok`
  - `manager_alive: true`
  - `dead: 0`
- `GET /api/v2/programs`: `22` programs.
- `GET /api/v2/tracks/active`: `1` active track (`TRANSCRIBED`).
- Frontend proxy parity check:
  - `GET http://127.0.0.1:3000/api/v2/programs`: `22` programs (matches backend).

Important incident status:

- Incident track `cbnjd022426cc-20260226T170933645379Z` was previously stuck in cloud submitted state.
- As of this snapshot, it appears `COMPLETED` in program data.
- Historical incident details and root-cause notes are in:
  - `docs/INCIDENT_CBNJD022426CC_2026-02-27.md`

## 3. What This System Is Supposed To Do

Product intent:

- Ingest broadcast programs.
- Transcribe source audio.
- Translate and polish subtitles in cloud.
- Finalize subtitle files.
- Burn subtitles into video.
- Track all program/track state in Postgres.
- Operate from a single UI (`Omega Pro`) for import, monitoring, and delivery.

Canonical flow:

1. User uploads media in frontend modal (`/api/v2/programs/upload`).
2. Ingest writes to local storage and creates sidecar metadata.
3. `omega_manager.py` picks up ingest work and creates/updates tracks.
4. Cloud Run worker processes translation artifacts from GCS.
5. `cloud_sync_service.py` syncs cloud outputs back to DB/filesystem.
6. Local finalize and burn produce SRT/video outputs.
7. UI reflects track progress from DB/API and socket updates.

## 4. Runtime Topology (Current)

Canonical local processes (PM2):

- `omega-cloud-sql-proxy`
- `omega-fastapi` (`uvicorn api_main:socket_app` on `127.0.0.1:8001`)
- `omega-manager` (`omega_manager.py`)
- `omega-cloud-sync` (`cloud_sync_service.py`)
- `omega-frontend` (Next standalone on `127.0.0.1:3000`)

Primary files:

- Process boot/contract: `start_omega.sh`
- Process teardown: `stop_all.sh`
- PM2 definitions: `ecosystem.config.js`
- API entry: `api_main.py`
- Manager/orchestrator: `omega_manager.py`
- Cloud sync daemon: `cloud_sync_service.py`
- Cloud worker entry: `omega_cloud_worker.py`
- Frontend shell: `omega-frontend/src/components/layout/AppShell.tsx`

## 5. Data And State Model

### 5.1 Core tables

ORM models are in `models.py`. Core entities:

- `programs`
- `tracks`
- `master_scripts`
- `track_deliveries`
- `script_edits`
- `stage_transitions`
- plus support tables (`error_log`, `system_state`, profiles/dropzones, etc.)

### 5.2 Dual DB access pattern

The system currently uses two DB layers:

- Async SQLAlchemy session (`db.py`) for FastAPI routers.
- Sync custom SQL layer (`omega_db.py`) for manager, cloud sync, and some route helpers.

This dual-path design is a major complexity and race-risk source.

### 5.3 Pipeline stages

Legacy uppercase stages are still the operational truth in many paths:

- `QUEUED`, `INGEST`, `TRANSCRIBED`
- `TRANSLATING_CLOUD_SUBMITTED`, `CLOUD_TRANSLATING`, `CLOUD_REVIEWING`
- `REVIEWED`, `FINALIZING`, `FINALIZED`, `BURNING`, `COMPLETED`
- terminal failure state often represented as `DEAD`

`state_machine.py` introduces normalized enums and transition validation, but legacy stage strings remain widely used.

## 6. Storage And Artifact Contracts

### 6.1 Local filesystem

Defined in `config.py`:

- `1_INBOX/` incoming files.
- `2_VAULT/` source video and data.
- `3_TRANSLATED_DONE/` approved translation JSON.
- `4_DELIVERY/SRT/` subtitle outputs.
- `4_DELIVERY/VIDEO/` burned outputs.

### 6.2 Cloud artifacts (GCS)

Per-job artifact paths in `gcs_jobs.py` (`GcsJobPaths`):

- `job.json`
- `skeleton.json`
- `audio.wav`
- `translation_checkpoint.json`
- `progress.json`
- `approved.json`
- optional review/status artifacts.

Cloud worker consumes `job.json` + `skeleton.json`, then writes translation outputs back to bucket.

## 7. API Surface (High Value Endpoints)

Under `/api/v2`:

- Programs:
  - `POST /programs/upload`
  - `GET /programs`
  - `GET /programs/{program_id}`
  - `POST /programs/{program_id}/tracks`
- Tracks:
  - `GET /tracks/active`
  - `GET /tracks/{track_id}`
  - `POST /tracks/{track_id}/retry`
  - `POST /tracks/{track_id}/approve`
  - `POST /tracks/{track_id}/finalize`
  - `POST /tracks/{track_id}/burn`
- Ops:
  - `GET /api/v2/ops/summary`
  - `GET /api/v2/ops/queue`
  - `POST /api/v2/ops/actions`
- Health:
  - `GET /api/v2/health`
  - `GET /api/v2/health/stuck`
  - `POST /api/v2/health/fix`

Realtime:

- Socket.IO mounted in `api_main.py` via `socket_app`.
- Frontend listens for `track_updated`.

## 8. Why Users Experienced "UI Works, Data Broken"

This pattern is real and reproducible:

1. Frontend can render while API is unstable or restarting.
2. Next.js proxy rewrites all `/api/*` to backend (`omega-frontend/next.config.ts`).
3. During backend restarts, frontend logs show:
   - `ECONNREFUSED` and `ECONNRESET` ("socket hang up", "Failed to proxy ...").
4. User sees shell and modal UI but stale/empty program data or failed uploads.

Evidence:

- `logs/frontend.err.log` contains multiple `Failed to proxy ... ECONNREFUSED/ECONNRESET` entries.
- `logs/fastapi.err.log` contains historical form parsing assertion and other route exceptions.

## 9. Confirmed Failure Modes

### 9.1 Cloud payload missing after ingest recovery (fixed)

- Root cause: recovery path re-transcribed but did not re-upload required cloud artifacts.
- Fix applied in `omega_manager.py`:
  - `_run_ingest_recovery` now uploads `job.json`, `skeleton.json`, and `audio.wav`.
  - `_run_translate_cloud` now fails fast if required blobs are missing.

### 9.2 Upload failures under dependency/runtime mismatch

- Historical FastAPI assertion: missing `python-multipart`.
- Fallback multipart parser exists now in `routers/programs.py`, but large uploads can still fail when backend restarts mid-request (proxy reset).

### 9.3 Process churn / restart side-effects

- Repeated process restarts interrupt long operations.
- Frontend can remain alive while backend is momentarily unavailable, producing confusing user-visible partial failures.

### 9.4 State machine split-brain

- Stage transitions are handled by multiple actors:
  - manager
  - cloud sync
  - health fix route
  - track retry route
- This increases risk of conflicting stage/status/meta updates.

### 9.5 Extremely large uncontrolled drift

- 206-file dirty tree in operational branch.
- Runtime, frontend, scripts, and docs are mixed in same drift.
- External devs cannot trust commit boundaries for safe rollback.

## 10. Handoff Stabilization Protocol (Do This Before Rebuild)

Freeze policy:

1. No feature work.
2. Only reliability patches with incident ID and rollback note.
3. All startup/tests run from a new clean handoff branch.

Immediate stabilization steps:

1. Clone fresh from current HEAD into a new clean workspace.
2. Copy environment/secrets only; do not copy runtime artifacts/log history.
3. Rebuild frontend standalone artifacts (`npm ci`, `npm run build` in `omega-frontend`).
4. Validate process contract with:
   - `./stop_all.sh`
   - `OMEGA_NO_TAIL=1 ./start_omega.sh`
   - `./scripts/stabilization/smoke_gate.sh`
   - `./scripts/stabilization/library_integrity_gate.sh`
5. Record baseline with `./scripts/stabilization/capture_baseline.sh`.

If any gate fails, do not proceed to rebuild planning until the failure is reproducible and root-caused.

## 11. Rebuild Recommendation (New System)

### 11.1 Rebuild goals

1. One orchestrator model.
2. One DB access pattern.
3. Explicit job event history.
4. Idempotent workers with retry contracts.
5. UI driven by query model, not inferred state.

### 11.2 Suggested target architecture

Core services:

1. API/BFF service (REST + websocket).
2. Workflow orchestrator (Temporal/Arq/Celery with explicit state machine).
3. Worker pool:
   - ingest worker
   - cloud translation dispatcher
   - cloud sync consumer
   - finalizer/burn worker
4. Artifact service wrapper (local + GCS abstraction).

Core contracts:

- Single job table with immutable event log table.
- Command handlers enforce allowed transitions.
- Every transition writes:
  - previous state
  - next state
  - actor
  - reason
  - correlation ID

### 11.3 Critical design constraints

1. Remove mixed sync/async DB stack.
2. Remove state-changing logic from health endpoints.
3. Move retry/dead-man behavior into orchestrator policies, not ad hoc route code.
4. Treat file writes and cloud writes as explicit steps with checksums and idempotency keys.

## 12. Migration Plan To Rebuild

### Phase 0: Capture truth (2-3 days)

1. Export API schema from current routes.
2. Snapshot current DB schema/data shape.
3. Capture 3 representative program runs end-to-end with artifact timelines.

### Phase 1: New domain model and contracts (3-5 days)

1. Define canonical stages and terminal states.
2. Define command/event schema.
3. Define artifact manifest per stage.

### Phase 2: Build orchestrator core (1-2 weeks)

1. Implement transition engine with strict validation.
2. Implement worker command queue and retries.
3. Implement dead-letter handling and manual intervention commands.

### Phase 3: Build new API read/write surfaces (1 week)

1. Read APIs for programs/tracks/ops.
2. Write APIs for upload/retry/approve/finalize/burn.
3. Websocket updates from event stream.

### Phase 4: Controlled shadow mode (1 week)

1. Run new orchestrator in observe-only mode on mirrored events.
2. Compare stage outcomes and latency against legacy system.
3. Fix parity gaps before cutover.

### Phase 5: Cutover and decomission (2-3 days)

1. Freeze imports for cutover window.
2. Switch UI/API to new backend.
3. Keep legacy manager as rollback path for 1 week.
4. Retire legacy components after acceptance metrics hold.

## 13. Acceptance Criteria For Rebuilt System

Reliability:

1. `>= 99%` successful ingest-to-burn completion over 50-run soak.
2. No orphan jobs without heartbeat/status updates for > 10 minutes.
3. Zero manual DB edits required for normal operations.

Performance:

1. 5 GB upload accepted and staged without 500/proxy reset.
2. Translation job start acknowledgment < 60 seconds.
3. Burn path throughput meets expected profile speeds.

Operability:

1. One-command health report shows true blocker root cause.
2. Replayable incident timeline from event log for any job.
3. Rebuild has deterministic startup and dependency checks.

## 14. Known Files External Team Should Read First

1. `docs/RUNBOOK.md`
2. `docs/SYSTEM_CANON.md`
3. `docs/stabilization/03-runtime-contract.md`
4. `docs/INCIDENT_CBNJD022426CC_2026-02-27.md`
5. `start_omega.sh`
6. `ecosystem.config.js`
7. `api_main.py`
8. `omega_manager.py`
9. `cloud_sync_service.py`
10. `omega_cloud_worker.py`
11. `routers/programs.py`
12. `routers/tracks.py`
13. `omega-frontend/next.config.ts`
14. `omega-frontend/src/components/common/ImportMediaModal.tsx`
15. `omega-frontend/src/store/programs.ts`

## 15. Practical Notes For Transfer

1. Hand over this document plus:
   - `docs/INCIDENT_CBNJD022426CC_2026-02-27.md`
   - latest `docs/stabilization/artifacts/*` bundle
2. Hand over exact env var set (without exposing secrets in git).
3. Hand over one reproducible failing upload case and one successful burn case.
4. Require external team to produce:
   - architecture decision record set
   - migration plan with rollback criteria
   - first production-readiness checklist before feature work resumes

---

If the external team needs a stricter package, create a clean branch that contains only:

1. this handoff document,
2. incident docs,
3. runtime contract docs,
4. no functional code changes.

This avoids shipping unresolved local drift as implicit behavior.
