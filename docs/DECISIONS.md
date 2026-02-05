# System Decisions (Handoff)

Date: 2026-02-03

## Goals
- Reliability, stability, future-proofing.
- One clear pipeline and one UI.
- Scales to 10–50 videos/week now, more later.
- Runs on a local Mac (Iceland) with cloud AI.

## Final Architecture Decisions
1. Single source of truth = Cloud Postgres.
   - No SQLite.
   - Local Mac connects over SSL.
2. Local Mac handles heavy media only.
   - Ingest, audio extraction, proxy creation, subtitle burn.
   - Original videos stay local.
3. Cloud handles AI only.
   - Upload proxy video + audio + transcript to GCS.
   - Run Gemini in Vertex AI.
4. One pipeline, two steps only.
   - Step 1: Translation.
   - Step 2: Review/Edit/Polish.
5. Single UI + single API.
   - No duplicate pipelines or legacy paths.

## Quality vs Cost Controls
- Step 1 (Translation): Gemini Pro for highest reasoning quality.
- Step 2 (Review/Polish): Gemini Flash for speed and cost.
- Keep proxy small (360p, 1 fps baseline + scene-change frames, mono AAC 64 kbps).
- Reuse the same proxy for all target languages (no re-upload per language).

## Latency Target
- Minutes, not hours.
- Keep GCS + Vertex in the same region as the cloud worker.

## Open Decisions (Need User Input)
1. Which managed Postgres provider? (Recommendation: Google Cloud SQL)
2. Which region? (e.g. europe-north1 vs us-central1)
3. Cost cap per week or per job?

## Immediate Next Steps (When Back)
1. Confirm cloud DB provider + region.
2. Lock pipeline diagram and state machine as source of truth.
3. Remove any remaining legacy pipeline paths.
4. Ensure all job state changes write to DB (no hidden state).
5. Set proxy defaults and Gemini model choices.
