# Azotus Runbook (Stable Hybrid)

## Stabilization Freeze
- Charter: [docs/stabilization/00-freeze-charter.md](/Users/haukurhauksson/Azotus/docs/stabilization/00-freeze-charter.md)
- Inventory/Risk Matrix: [docs/stabilization/01-inventory-and-risk-matrix.md](/Users/haukurhauksson/Azotus/docs/stabilization/01-inventory-and-risk-matrix.md)
- Owners template: [docs/stabilization/owners-template.md](/Users/haukurhauksson/Azotus/docs/stabilization/owners-template.md)
- Execution log: [docs/stabilization/02-execution-log.md](/Users/haukurhauksson/Azotus/docs/stabilization/02-execution-log.md)
- Runtime contract: [docs/stabilization/03-runtime-contract.md](/Users/haukurhauksson/Azotus/docs/stabilization/03-runtime-contract.md)
- Scripts: [scripts/stabilization/README.md](/Users/haukurhauksson/Azotus/scripts/stabilization/README.md)

Required gate commands:
- `./scripts/stabilization/smoke_gate.sh`
- `./scripts/stabilization/library_integrity_gate.sh`
- `./scripts/stabilization/capture_baseline.sh`
- `OMEGA_RESTART_LOOPS=10 ./scripts/stabilization/restart_gate.sh`
- `./scripts/stabilization/release_gate.sh`

## Architecture (Canonical)
- **Local Mac**: ingest video, extract audio, make proxy/thumbnail, finalize SRT, burn video.
- **Cloud**: Vertex AI translation + review/polish, artifacts in GCS.
- **DB**: Cloud SQL (Postgres) is the single source of truth.
- **UI**: Programs/Tracks (API v2) only.

## Required Environment
Set these in `.env` or `.omega_secrets` (and keep both gitignored).

Core:
- `DB_TYPE=postgres`
- `DB_DSN=postgresql://...` **or** `DB_INSTANCE_CONNECTION_NAME`, `DB_USER`, `DB_PASS`, `DB_NAME`
- `OMEGA_CLOUD_PIPELINE=1`
- `OMEGA_JOBS_BUCKET=...`
- `OMEGA_JOBS_PREFIX=jobs`
- `OMEGA_CLOUD_RUN_JOB=omega-cloud-worker`
- `OMEGA_CLOUD_RUN_REGION=us-central1`
- `OMEGA_CLOUD_PROJECT=...`
- `ELEVENLABS_API_KEY=...`

Station identity:
- `OMEGA_STATION_ID=iceland-01` (unique per Mac)
- `OMEGA_UI_SCOPE=station` (or `all` for admin view)
- `OMEGA_STATION_CLAIM_UNASSIGNED=0` (set to 1 only to recover legacy jobs)

Optional:
- `OMEGA_CLOUD_SYNC_ENABLED=1`
- `OMEGA_CLOUD_SYNC_POLL_SECONDS=60`

## Start / Stop
- Install PM2 once: `./scripts/install_pm2.sh` (or `npm i -g pm2`)
- If PM2 is installed locally, set `OMEGA_PM2_BIN=/absolute/path/to/pm2`
- Start: `OMEGA_NO_TAIL=1 ./start_omega.sh`
- Stop: `./stop_all.sh`
- Status: `pm2 status` (or use `$OMEGA_PM2_BIN status`)
- Logs: `pm2 logs --lines 100` (or use `$OMEGA_PM2_BIN logs --lines 100`)


## Pipeline (Local-first + Cloud)
1. **Import video** via UI (`/media`) → job+program created.
2. **Local ingest**: move video to Vault, extract audio, thumbnail.
3. **Proxy generation**: 360p proxy (with low‑bitrate audio) is created and uploaded to GCS for Vertex/Gemini context caching.
4. **(Optional) Vision scan**: if `OMEGA_MULTIMODAL_ENABLED=1`, run vision scan + danger zones.
5. **Transcription**: ElevenLabs produces the skeleton JSON (shared across tracks).
6. **Cloud translation**: Cloud Run worker runs the 2‑step Gemini pipeline (Translate → Review/Polish) on the proxy.
7. **Review/Polish**: review + polish in the same step, then approved JSON returned.
8. **Finalize**: local station formats SRT + burns video.

Proxy details:
- Generated in `omega_manager._run_multimodal_pipeline()` and uploaded to `gs://<bucket>/<prefix>/<job_id>/proxy_360p.mp4`.
- Consumed in `omega_cloud_worker.GeminiTranslationPipeline._create_context_cache()` for Vertex AI.

## Multi-language Workflow (Clean)
1. Import a video via UI.
2. Add tracks per language (e.g., `is`, `nl`).
3. Each track is processed independently in cloud.
4. Approved translations sync back; local station finalizes + burns per track.

## Station Safety
- Each station only processes jobs tagged with its `station_id`.
- Jobs without station_id are ignored unless `OMEGA_STATION_CLAIM_UNASSIGNED=1`.

## Troubleshooting
- Check logs: `logs/manager.err.log`, `logs/fastapi.err.log`, `logs/frontend.err.log`, `logs/cloud_sync.err.log`
- Health endpoint: `/api/health`
- Ops view: `/ops` in UI (Programs/Tracks shell)

## Cloud Worker Deploy (GitOps)
Use Cloud Build instead of manual laptop deploys.

1. Connect the repo to Google Cloud Build.
2. Create a trigger on push to your main branch.
3. Cloud Build uses `cloudbuild.yaml` to:
   - Build the worker image from `cloud/Dockerfile`
   - Push `gcr.io/sermon-translator-system/omega-cloud-worker`
   - Update the Cloud Run job `omega-cloud-worker`

Notes:
- Edit substitutions in `cloudbuild.yaml` if project/region/job/image change.
- Manual deploy scripts live in `scripts/legacy/` and should not be used.
