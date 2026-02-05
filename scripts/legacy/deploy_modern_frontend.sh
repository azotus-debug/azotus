#!/bin/bash
# Deploy Modern Omega Frontend to Cloud Run

set -e

# Load environment from root (assuming script run from root or relative)
SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR/.." || exit 1
ENV_FILE=".env"

if [ -f "$ENV_FILE" ]; then
    set -o allexport
    source "$ENV_FILE"
    set +o allexport
    echo "📄 Loaded configuration from .env"
fi

PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
SERVICE_NAME="omega-next-frontend"
BACKEND_URL="https://omega-cloud-manager-283123700702.us-central1.run.app"

echo "🚀 Deploying Modern Frontend..."
echo "   Project: $PROJECT_ID"
echo "   Region: $REGION"
echo "   Service: $SERVICE_NAME"
echo "   Backend: $BACKEND_URL"
echo ""

# Build and deploy from the omega-frontend directory
# We must include the root context? No, frontend is self contained usually.
# But if it needs shared types... checking. Assuming self contained for now.

 gcloud run deploy $SERVICE_NAME \
    --source omega-frontend \
    --project $PROJECT_ID \
    --region $REGION \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --min-instances 1 \
    --max-instances 5 \
    --set-env-vars "BACKEND_URL=${BACKEND_URL},NEXT_PUBLIC_API_URL=${BACKEND_URL}"

echo ""
echo "✅ Deployment complete!"
