#!/bin/bash
# Deploy Omega Review Portal to Cloud Run

set -e

# Load environment from root (assuming script run from root or relative)
SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR" || exit 1
ENV_FILE="../../.env"

if [ -f "$ENV_FILE" ]; then
    set -o allexport
    source "$ENV_FILE"
    set +o allexport
    echo "📄 Loaded configuration from .env"
fi

PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
SERVICE_NAME="omega-review"
BUCKET="${OMEGA_JOBS_BUCKET:-omega-jobs-subtitle-project}"
PREFIX="${OMEGA_JOBS_PREFIX:-jobs}"

echo "🚀 Deploying Omega Review Portal..."
echo "   Project: $PROJECT_ID"
echo "   Region: $REGION"
echo "   Service: $SERVICE_NAME"
echo "   Bucket:  $BUCKET"
echo ""

# Build and deploy
gcloud run deploy $SERVICE_NAME \
    --source . \
    --project $PROJECT_ID \
    --region $REGION \
    --allow-unauthenticated \
    --memory 256Mi \
    --cpu 1 \
    --max-instances 3 \
    --set-env-vars "OMEGA_JOBS_BUCKET=${BUCKET},OMEGA_JOBS_PREFIX=${PREFIX},OMEGA_REVIEW_SECRET=${OMEGA_REVIEW_SECRET},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"

echo ""
echo "✅ Deployment complete!"
echo ""

# Get the URL
URL=$(gcloud run services describe $SERVICE_NAME --project $PROJECT_ID --region $REGION --format 'value(status.url)')
echo "🌐 Portal URL: $URL"
