#!/bin/bash
set -euo pipefail

PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
SERVICE_NAME="${OMEGA_CLOUD_MANAGER_SERVICE:-omega-cloud-manager}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

if [ -f .env ]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

if [ -z "${OMEGA_JOBS_BUCKET:-}" ]; then
  echo "OMEGA_JOBS_BUCKET must be set in .env or env vars."
  exit 1
fi

if [ -z "${OMEGA_CLOUD_RUN_JOB:-}" ]; then
  echo "OMEGA_CLOUD_RUN_JOB must be set in .env or env vars."
  exit 1
fi

ENV_VARS=(
  "OMEGA_JOBS_BUCKET=${OMEGA_JOBS_BUCKET}"
  "OMEGA_JOBS_PREFIX=${OMEGA_JOBS_PREFIX:-jobs}"
  "OMEGA_CLOUD_RUN_JOB=${OMEGA_CLOUD_RUN_JOB}"
  "OMEGA_CLOUD_RUN_REGION=${REGION}"
  "OMEGA_CLOUD_PROJECT=${PROJECT_ID}"
  "OMEGA_CLOUD_MANAGER_LOOP=1"
  "OMEGA_CLOUD_MANAGER_AUTOSTART=1"
  "OMEGA_CLOUD_MANAGER_POLL_SECONDS=${OMEGA_CLOUD_MANAGER_POLL_SECONDS:-5}"
)

# if [ -n "${OMEGA_CLOUD_MANAGER_TOKEN:-}" ]; then
#   ENV_VARS+=("OMEGA_CLOUD_MANAGER_TOKEN=${OMEGA_CLOUD_MANAGER_TOKEN}")
# fi

if [ -n "${DB_DSN:-}" ] || [ -n "${DATABASE_URL:-}" ]; then
  DB_DSN_VALUE="${DB_DSN:-$DATABASE_URL}"
  ENV_VARS+=("DB_DSN=${DB_DSN_VALUE}")
else
  if [ -z "${DB_INSTANCE_CONNECTION_NAME:-}" ] || [ -z "${DB_PASS:-}" ]; then
    echo "DB_INSTANCE_CONNECTION_NAME and DB_PASS must be set for Cloud SQL."
    exit 1
  fi
  ENV_VARS+=(
    "DB_INSTANCE_CONNECTION_NAME=${DB_INSTANCE_CONNECTION_NAME}"
    "DB_USER=${DB_USER:-omega_user}"
    "DB_NAME=${DB_NAME:-omega_db}"
    "DB_PASS=${DB_PASS}"
    "DB_IP_TYPE=${DB_IP_TYPE:-public}"
  )
fi

echo "Building Cloud Manager image..."
gcloud builds submit \
  --config cloud/cloud_manager/cloudbuild.yaml \
  --substitutions=_IMAGE="${IMAGE}" \
  --project "${PROJECT_ID}" \
  .

echo "Deploying Cloud Manager to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --no-cpu-throttling \
  --cpu 2 \
  --memory 2Gi \
  --concurrency 1 \
  --timeout 900 \
  --set-env-vars "$(IFS=,; echo "${ENV_VARS[*]}")"

echo "Cloud Manager deployed."
