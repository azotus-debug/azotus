# Gemini Handoff: Cloud Sync + Local Parity

Purpose: help connect the GCS/Vertex environment to the Azotus rebuild and ensure cloud config matches local.

## Project Context
- Repo root: `/Users/haukurhauksson/Azotus`
- GCP Project ID: `sermon-translator-system`
- GCS Bucket: `omega-jobs-subtitle-project`
- Cloud Run Job: `omega-cloud-worker`
- Goal: keep cloud runtime aligned with local settings.

## Key Local Decisions (Must Mirror Cloud)
- Proxy spec sent to Vertex:
  - 360p (640x360)
  - 1 fps baseline + extra frames on scene changes
  - Mono AAC ~64 kbps
- Models:
  - Translation: `gemini-3-pro-preview`
  - Review/Polish: `gemini-3-flash-preview`

## Files to Read
- `config.py` (model defaults)
- `workers/proxy_generator.py` (proxy spec + scene-change sampling)
- `omega_cloud_worker.py` (uses `MODEL_TRANSLATOR`, `MODEL_EDITOR`)
- `config/defaults.py` (GCP defaults)

## What to Verify in Cloud Run Job
1. Environment variables do not override local defaults:
   - Ensure `MODEL_TRANSLATOR`, `MODEL_EDITOR`
     are **unset** or set to the values above.
2. Ensure the job still points to:
   - `OMEGA_CLOUD_PROJECT=sermon-translator-system`
   - `OMEGA_GCS_BUCKET=omega-jobs-subtitle-project`
   - `OMEGA_GCS_PREFIX=jobs`
   - `GEMINI_LOCATION` matches the bucket region (currently `us-central1`).
3. Service account permissions:
   - Storage read/write on the bucket
   - Vertex AI user

## Expected Flow (Cloud)
1. Local app uploads:
   - proxy video: `jobs/<job_id>/proxy_360p.mp4`
   - audio: `jobs/<job_id>/audio.wav`
   - transcript/skeleton JSON
2. Cloud worker runs:
   - Step 1: Translation (Pro)
   - Step 2: Review/Polish (Flash)
3. Results saved back to GCS and mirrored locally by sync.

## If Cloud Doesn’t Match Local
Update Cloud Run Job env vars to match the config above.

## Contact Points
If anything is unclear, ask for:
- `config.py`
- `workers/proxy_generator.py`
- `omega_cloud_worker.py`
