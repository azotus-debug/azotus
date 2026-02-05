#!/bin/bash
set -e

# Configuration
PROJECT_ID="sermon-translator-system"
REGION="us-central1"
BACKEND_SERVICE="omega-backend"
FRONTEND_SERVICE="omega-dashboard"
BACKEND_IMAGE="omega-backend"
FRONTEND_IMAGE="omega-frontend"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting Omega Dashboard Cloud Deployment...${NC}"

# Source env vars cleanly
if [ -f .env ]; then
    set -o allexport
    source .env
    set +o allexport
else
    echo "❌ .env file not found!"
    exit 1
fi

if [ -z "$DB_INSTANCE_CONNECTION_NAME" ] || [ -z "$DB_PASS" ]; then
    echo "❌ Missing DB credentials in .env"
    exit 1
fi

echo -e "${BLUE}☁️  Project: $PROJECT_ID${NC}"
echo -e "${BLUE}☁️  Region:  $REGION${NC}"

# 1. Deploy Backend
echo -e "\n${GREEN}📦 Building Backend Service ($BACKEND_IMAGE)...${NC}"
# Use custom cloudbuild file to specify Dockerfile.backend
gcloud builds submit . \
  --config cloudbuild_backend.yaml \
  --substitutions=_IMAGE="gcr.io/$PROJECT_ID/$BACKEND_IMAGE" \
  --project "$PROJECT_ID" \
  --quiet

echo -e "\n${GREEN}🚀 Deploying Backend to Cloud Run...${NC}"
gcloud run deploy "$BACKEND_SERVICE" \
  --image "gcr.io/$PROJECT_ID/$BACKEND_IMAGE" \
  --platform managed \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --set-env-vars "DB_TYPE=postgres" \
  --set-env-vars "DB_INSTANCE_CONNECTION_NAME=$DB_INSTANCE_CONNECTION_NAME" \
  --set-env-vars "DB_USER=${DB_USER:-omega_user}" \
  --set-secrets "DB_PASS=db-password:latest" \
  --set-env-vars "DB_NAME=${DB_NAME:-omega_db}" \
  --set-env-vars "OMEGA_ADMIN_TOKEN=${OMEGA_ADMIN_TOKEN:-temp_secret}" \
  --cpu 2 \
  --memory 2Gi \
  --concurrency 20 \
  --max-instances 10 \
  --min-instances 0 \
  --timeout 900 \
  --quiet

# Get Backend URL
BACKEND_URL=$(gcloud run services describe "$BACKEND_SERVICE" --platform managed --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')
echo -e "${BLUE}✅ Backend Active at: $BACKEND_URL${NC}"

# 2. Deploy Frontend
echo -e "\n${GREEN}📦 Building Frontend Service ($FRONTEND_IMAGE)...${NC}"
cd omega-frontend
gcloud builds submit --tag "gcr.io/$PROJECT_ID/$FRONTEND_IMAGE" . --project "$PROJECT_ID" --quiet

echo -e "\n${GREEN}🚀 Deploying Frontend to Cloud Run...${NC}"
gcloud run deploy "$FRONTEND_SERVICE" \
  --image "gcr.io/$PROJECT_ID/$FRONTEND_IMAGE" \
  --platform managed \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --set-env-vars "BACKEND_URL=$BACKEND_URL" \
  --quiet

# Get Frontend URL
FRONTEND_URL=$(gcloud run services describe "$FRONTEND_SERVICE" --platform managed --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')

echo -e "\n========================================================"
echo -e "${GREEN}✅ DEPLOYMENT COMPLETE!${NC}"
echo -e "🌍 Dashboard URL: ${BLUE}$FRONTEND_URL${NC}"
echo -e "🔗 Backend URL:   $BACKEND_URL"
echo -e "========================================================"
