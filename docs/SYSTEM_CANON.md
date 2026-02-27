# Azotus System Canon (2026-02-22)

This file defines the canonical runtime contract for the current system.

## 1) Runtime Services

- `omega-fastapi` (Socket.IO + REST): port `8001`
- `omega-manager` (pipeline orchestrator)
- `omega-frontend` (Next.js): port `3000`
- Active frontend routes are unified through `/` (AppShell); `/media`, `/edit`, `/deliver`, `/settings` now redirect into AppShell views.

Optional unified supervisor:

- PM2 config: `ecosystem.config.js`
- Start with PM2 (opt-in): `OMEGA_USE_PM2=1 ./start_omega.sh`

Legacy `dashboard.py` and legacy FastAPI route mounts are no longer part of runtime supervision.

## 2) Style Contract

- Canonical subtitle style: `RUV_BOX`
- Acceptable aliases (mapped at burn time): `Classic`, `RuvBox`, `RUV_BOX`
- Burn method map resolves canonical style to direct ASS burn (`RuvBox`)

## 3) Approval/Burn Contract

- Human approval sets:
  - `stage = FINALIZED`
  - `status = "Approved for Burn"`
  - `meta.burn_approved = true`
- Manager pre-burn gate accepts `meta.burn_approved` as source of truth.

## 4) Failure Contract

- Worker task exceptions now persist traceback and transition retriable permanent errors to `FAILED`.
- Terminal guardrail remains `DEAD` after retry limit.

## 5) Finalizer Contract

- V2 finalizer entrypoint: `workers/finalizer/main.py::finalize`
- API uses typed path input and tuple return `(srt_path, normalized_path)`.
- Coverage gate is token-based with structured output:
  - `passed`, `min_ratio`, `coverage_ratio`, `total_tokens`, `missing_tokens`, `missing_samples`

## 6) Ingest Contract

- Canonical ingest endpoint for frontend import modal:
  - `POST /api/v2/programs/upload`
- Supported ingest modes:
  - `full_pipeline`, `quick_burn`, `skip_transcription`, `srt_update`
- `full_pipeline` sidecar now defaults to `subtitle_style = RUV_BOX`.
