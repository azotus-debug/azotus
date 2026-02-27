# Incident Handoff: CBNJD022426CC Translation Stall

## Scope
- Program: `CBNJD022426CC.mxf`
- Track ID: `53eb21f4-bc45-4af6-a035-15bdace1a1ef`
- Job ID: `cbnjd022426cc-20260226T170933645379Z`
- Date opened: `2026-02-26`
- Last update: `2026-02-27`

## User-Visible Symptom
- UI remains at `TRANSLATING_CLOUD_SUBMITTED` / `Submitted to Cloud` (~40%).
- No SRT/video delivered.

## Confirmed Root Cause (Local Pipeline)
- During initial ingest, manager received `SIGINT` (`2026-02-26 12:12:07`).
- Recovery path (`_run_ingest_recovery`) re-transcribed successfully, but did **not** re-upload cloud payload artifacts (`job.json`, `skeleton.json`, `audio.wav`) for this job.
- Cloud translation was repeatedly triggered against a missing payload, producing long `TRANSLATING_CLOUD_SUBMITTED` stalls.

## Code Fixes Applied
1. `omega_manager.py`
- Recovery path now uploads required cloud artifacts after re-transcription when cloud pipeline is enabled.
- Added cloud trigger guardrail: `_run_translate_cloud` now verifies `job.json` and `skeleton.json` exist in GCS before triggering Cloud Run; otherwise it fails fast with explicit error.

2. Previously applied hardening (already in workspace)
- `cloud_sync_service.py`: stage-specific dead-man behavior (submitted stage can be disabled).
- `omega_manager.py`: cloud stall retry cooldown and no hard-kill loop for cloud stages.
- `routers/tracks.py`: retry metadata/timeline reset to prevent immediate false re-stall.

## Recovery Actions Performed
- Manually backfilled missing cloud artifacts to:
  - `gs://omega-jobs-subtitle-project/jobs/cbnjd022426cc-20260226T170933645379Z/job.json`
  - `gs://omega-jobs-subtitle-project/jobs/cbnjd022426cc-20260226T170933645379Z/skeleton.json`
  - `gs://omega-jobs-subtitle-project/jobs/cbnjd022426cc-20260226T170933645379Z/audio.wav`
- Retried track translation (`force=1`), which triggered Cloud Run again.

## Current State (as observed)
- Track still shows `TRANSLATING_CLOUD_SUBMITTED` in API.
- GCS now contains payload artifacts plus:
  - `translation_checkpoint.json` updated at `2026-02-27 16:38:39 UTC`.
- `progress.json`, `approved.json`, and reviewed artifacts are still absent for this job.

## What This Means
- Local missing-payload bug is fixed and guarded against recurrence.
- This specific execution is now progressing past zero (checkpoint created), but appears to stall inside cloud worker processing after checkpoint initialization.

## Handoff Tasks For Cloud/Backend Dev
1. Inspect Cloud Run operation for job:
- Operation (latest trigger): `projects/sermon-translator-system/locations/us-central1/operations/7ae598ce-4677-4818-a40a-6b940392240c`

2. Inspect cloud worker logs for this `job_id`:
- Look for chunk loop start/end, model invocation failures, credential errors, quota, timeout, or unhandled exceptions after checkpoint write.

3. Verify expected artifacts are produced in order:
- `progress.json` updates during chunking/review
- `translation_draft.json` (if used)
- `approved.json` or `*_REVIEWED.json`

4. If operation is stuck/failed:
- Re-run cloud worker for this job once after confirming logs.
- Keep new guardrail in place (do not bypass missing-payload check).

## Operational Note
- This repository has many unrelated local modifications. Do not assume clean git history while reviewing this incident.
