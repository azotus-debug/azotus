#!/bin/bash
# =============================================================================
# Omega TV Subtitle System - Cloud SQL Setup Script
# =============================================================================
# This script creates and configures a Cloud SQL PostgreSQL instance for the
# Omega subtitle workflow system.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Sufficient GCP permissions (Cloud SQL Admin)
#   - docs/CLOUD_SQL_SCHEMA.sql exists in the project
#
# Usage:
#   ./scripts/setup_cloud_sql.sh
#
# After running:
#   1. Save the generated password securely
#   2. Update .env with the connection details
#   3. For local development, use Cloud SQL Auth Proxy (see instructions below)
# =============================================================================

set -e  # Exit on any error

# Configuration
PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
INSTANCE_NAME="${OMEGA_CLOUD_SQL_INSTANCE_NAME:-omega-db}"
DATABASE="omega"
DB_USER="omega_user"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory (for finding schema file)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SCHEMA_FILE="$PROJECT_ROOT/docs/CLOUD_SQL_SCHEMA.sql"

echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Omega TV - Cloud SQL Setup                ${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""
echo -e "Project:  ${GREEN}$PROJECT_ID${NC}"
echo -e "Region:   ${GREEN}$REGION${NC}"
echo -e "Instance: ${GREEN}$INSTANCE_NAME${NC}"
echo -e "Database: ${GREEN}$DATABASE${NC}"
echo ""

# Check if schema file exists
if [ ! -f "$SCHEMA_FILE" ]; then
    echo -e "${RED}Error: Schema file not found at $SCHEMA_FILE${NC}"
    echo "Please ensure docs/CLOUD_SQL_SCHEMA.sql exists."
    exit 1
fi

# -----------------------------------------------------------------------------
# Step 1: Enable required APIs
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/6] Enabling Cloud SQL Admin API...${NC}"
gcloud services enable sqladmin.googleapis.com --project="$PROJECT_ID" 2>/dev/null || true
echo -e "${GREEN}      API enabled.${NC}"

# -----------------------------------------------------------------------------
# Step 2: Check if instance already exists
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[2/6] Checking for existing instance...${NC}"
EXISTING=$(gcloud sql instances list --project="$PROJECT_ID" --filter="name=$INSTANCE_NAME" --format="value(name)" 2>/dev/null || true)

if [ -n "$EXISTING" ]; then
    echo -e "${YELLOW}      Instance '$INSTANCE_NAME' already exists.${NC}"
    read -p "      Do you want to continue with existing instance? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Exiting."
        exit 0
    fi
    INSTANCE_EXISTS=true
else
    INSTANCE_EXISTS=false
fi

# -----------------------------------------------------------------------------
# Step 3: Create Cloud SQL instance (if needed)
# -----------------------------------------------------------------------------
if [ "$INSTANCE_EXISTS" = false ]; then
    echo -e "${YELLOW}[3/6] Creating Cloud SQL instance...${NC}"
    echo -e "      This takes 5-10 minutes. Please wait..."

    gcloud sql instances create "$INSTANCE_NAME" \
        --database-version=POSTGRES_15 \
        --tier=db-f1-micro \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --storage-type=SSD \
        --storage-size=10GB \
        --availability-type=zonal \
        --no-assign-ip \
        --enable-google-private-path

    echo -e "${GREEN}      Instance created successfully.${NC}"
else
    echo -e "${YELLOW}[3/6] Skipping instance creation (already exists).${NC}"
fi

# -----------------------------------------------------------------------------
# Step 4: Create database
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[4/6] Creating database '$DATABASE'...${NC}"
DB_EXISTS=$(gcloud sql databases list --instance="$INSTANCE_NAME" --project="$PROJECT_ID" --filter="name=$DATABASE" --format="value(name)" 2>/dev/null || true)

if [ -z "$DB_EXISTS" ]; then
    gcloud sql databases create "$DATABASE" \
        --instance="$INSTANCE_NAME" \
        --project="$PROJECT_ID"
    echo -e "${GREEN}      Database created.${NC}"
else
    echo -e "${YELLOW}      Database already exists.${NC}"
fi

# -----------------------------------------------------------------------------
# Step 5: Create user with secure password
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[5/6] Creating database user '$DB_USER'...${NC}"
USER_EXISTS=$(gcloud sql users list --instance="$INSTANCE_NAME" --project="$PROJECT_ID" --filter="name=$DB_USER" --format="value(name)" 2>/dev/null || true)

# Generate a secure password
DB_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)

if [ -z "$USER_EXISTS" ]; then
    gcloud sql users create "$DB_USER" \
        --instance="$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --password="$DB_PASSWORD"
    echo -e "${GREEN}      User created.${NC}"
else
    echo -e "${YELLOW}      User already exists. Updating password...${NC}"
    gcloud sql users set-password "$DB_USER" \
        --instance="$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --password="$DB_PASSWORD"
    echo -e "${GREEN}      Password updated.${NC}"
fi

# -----------------------------------------------------------------------------
# Step 6: Apply schema
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[6/6] Applying database schema...${NC}"
echo -e "      Note: Schema will be applied via Cloud SQL Auth Proxy."
echo -e "      See instructions below for manual schema application."

# Build connection string
CONNECTION_INSTANCE="$PROJECT_ID:$REGION:$INSTANCE_NAME"
CONNECTION_STRING="postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/$DATABASE"

# -----------------------------------------------------------------------------
# Output Summary
# -----------------------------------------------------------------------------
echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  Setup Complete!                           ${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo -e "${BLUE}Connection Details:${NC}"
echo "---------------------------------------------------"
echo -e "Instance Connection Name: ${GREEN}$CONNECTION_INSTANCE${NC}"
echo -e "Database:                 ${GREEN}$DATABASE${NC}"
echo -e "User:                     ${GREEN}$DB_USER${NC}"
echo -e "Password:                 ${GREEN}$DB_PASSWORD${NC}"
echo "---------------------------------------------------"
echo ""
echo -e "${BLUE}Environment Variables (add to .env):${NC}"
echo "---------------------------------------------------"
cat << EOF
# Cloud SQL Configuration
OMEGA_CLOUD_SQL=1
OMEGA_CLOUD_SQL_INSTANCE=$CONNECTION_INSTANCE
OMEGA_PG_CONNECTION_STRING=$CONNECTION_STRING
OMEGA_DUAL_WRITE=1
OMEGA_PG_PRIMARY=0
EOF
echo "---------------------------------------------------"
echo ""
echo -e "${YELLOW}IMPORTANT: Save the password securely!${NC}"
echo ""

# -----------------------------------------------------------------------------
# Cloud SQL Auth Proxy Instructions
# -----------------------------------------------------------------------------
echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Cloud SQL Auth Proxy Setup (Local Dev)    ${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""
echo "For local development, use the Cloud SQL Auth Proxy to connect securely."
echo ""
echo -e "${YELLOW}1. Install Cloud SQL Auth Proxy:${NC}"
echo ""
echo "   # macOS (Apple Silicon)"
echo "   curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.1/cloud-sql-proxy.darwin.arm64"
echo "   chmod +x cloud-sql-proxy"
echo "   sudo mv cloud-sql-proxy /usr/local/bin/"
echo ""
echo "   # macOS (Intel)"
echo "   curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.1/cloud-sql-proxy.darwin.amd64"
echo "   chmod +x cloud-sql-proxy"
echo "   sudo mv cloud-sql-proxy /usr/local/bin/"
echo ""
echo "   # Or via Homebrew"
echo "   brew install cloud-sql-proxy"
echo ""
echo -e "${YELLOW}2. Start the proxy (in a separate terminal):${NC}"
echo ""
echo "   cloud-sql-proxy $CONNECTION_INSTANCE --port=5432"
echo ""
echo -e "${YELLOW}3. Apply the schema:${NC}"
echo ""
echo "   # Using psql"
echo "   PGPASSWORD='$DB_PASSWORD' psql -h localhost -p 5432 -U $DB_USER -d $DATABASE -f docs/CLOUD_SQL_SCHEMA.sql"
echo ""
echo "   # Or interactively"
echo "   PGPASSWORD='$DB_PASSWORD' psql -h localhost -p 5432 -U $DB_USER -d $DATABASE"
echo ""
echo -e "${YELLOW}4. Verify connection:${NC}"
echo ""
echo "   PGPASSWORD='$DB_PASSWORD' psql -h localhost -p 5432 -U $DB_USER -d $DATABASE -c '\\dt'"
echo ""
echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Cloud Run Service Connection              ${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""
echo "For Cloud Run services, add the Cloud SQL connection:"
echo ""
echo "   gcloud run services update omega-cloud-worker \\"
echo "     --add-cloudsql-instances=$CONNECTION_INSTANCE \\"
echo "     --set-env-vars=\"OMEGA_CLOUD_SQL=1,OMEGA_CLOUD_SQL_INSTANCE=$CONNECTION_INSTANCE\" \\"
echo "     --region=$REGION"
echo ""
echo -e "${GREEN}Done!${NC}"
