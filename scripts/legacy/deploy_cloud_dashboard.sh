#!/bin/bash
# =============================================================================
# Omega Cloud Dashboard - Deployment Script
# =============================================================================
# Deploys the Flask dashboard to Cloud Run for "access from anywhere"
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - .env file with DB credentials
#   - Cloud SQL instance accessible
#
# Usage:
#   bash scripts/deploy_cloud_dashboard.sh
# =============================================================================

set -euo pipefail

# Configuration
PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
SERVICE_NAME="${OMEGA_CLOUD_DASHBOARD_SERVICE:-omega-cloud-dashboard}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Load environment
if [ -f .env ]; then
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
fi

# Validate required variables
if [ -z "${DB_INSTANCE_CONNECTION_NAME:-}" ] && [ -z "${DB_DSN:-}" ]; then
    echo "❌ Error: DB_INSTANCE_CONNECTION_NAME or DB_DSN must be set"
    exit 1
fi

if [ -z "${DB_PASS:-}" ] && [ -z "${DB_DSN:-}" ]; then
    echo "❌ Error: DB_PASS must be set for Cloud SQL connection"
    exit 1
fi

# Build environment variables for Cloud Run
ENV_VARS=(
    "DB_TYPE=postgres"
    "OMEGA_CLOUD_PROJECT=${PROJECT_ID}"
)

# Add database connection
if [ -n "${DB_DSN:-}" ]; then
    ENV_VARS+=("DB_DSN=${DB_DSN}")
else
    ENV_VARS+=(
        "DB_INSTANCE_CONNECTION_NAME=${DB_INSTANCE_CONNECTION_NAME}"
        "DB_USER=${DB_USER:-omega_user}"
        "DB_NAME=${DB_NAME:-omega_db}"
        "DB_PASS=${DB_PASS}"
    )
fi

# Add optional configurations
if [ -n "${OMEGA_ADMIN_TOKEN:-}" ]; then
    ENV_VARS+=("OMEGA_ADMIN_TOKEN=${OMEGA_ADMIN_TOKEN}")
fi

if [ -n "${OMEGA_JOBS_BUCKET:-}" ]; then
    ENV_VARS+=("OMEGA_JOBS_BUCKET=${OMEGA_JOBS_BUCKET}")
fi

if [ -n "${OMEGA_JOBS_PREFIX:-}" ]; then
    ENV_VARS+=("OMEGA_JOBS_PREFIX=${OMEGA_JOBS_PREFIX}")
fi

echo "🚀 Building Cloud Dashboard image..."
gcloud builds submit \
    --config cloud/cloud_dashboard/cloudbuild.yaml \
    --substitutions=_IMAGE="${IMAGE}" \
    . \
    --project "${PROJECT_ID}"

echo "🌐 Deploying Cloud Dashboard to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE}" \
    --platform managed \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --allow-unauthenticated \
    --min-instances 0 \
    --max-instances 3 \
    --cpu 1 \
    --memory 1Gi \
    --timeout 300 \
    --concurrency 80 \
    --set-env-vars "$(IFS=,; echo "${ENV_VARS[*]}")"

# Get the deployed URL
DASHBOARD_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --platform managed \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --format 'value(status.url)')

echo ""
echo "✅ Cloud Dashboard deployed successfully!"
echo ""
echo "📍 Dashboard URL: ${DASHBOARD_URL}"
echo ""
echo "🔧 Next steps:"
echo "   1. Test the dashboard: curl ${DASHBOARD_URL}/healthz"
echo "   2. Update DNS: Point dashboard.azotus.io to ${DASHBOARD_URL}"
echo "   3. Access from anywhere!"
echo ""
