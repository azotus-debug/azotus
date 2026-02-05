#!/bin/bash
set -e

# Omega Backup Script
# Usage: ./scripts/backup_omega.sh

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="backups/omega_backup_$TIMESTAMP"

echo "🛡️ Starting Backup to $BACKUP_DIR..."

mkdir -p "$BACKUP_DIR"

# 1. Backup Database
if [ -f "production.db" ]; then
    cp production.db "$BACKUP_DIR/"
    echo "✅ Database backed up."
else
    echo "⚠️  production.db not found (skipping)"
fi

# 2. Backup Configs (Secrets)
if [ -f ".env" ]; then
    cp .env "$BACKUP_DIR/"
    echo "✅ .env backed up."
fi

if [ -f ".omega_secrets" ]; then
    cp .omega_secrets "$BACKUP_DIR/"
    echo "✅ .omega_secrets backed up."
fi

# 3. Create Zip Archive
zip -r "${BACKUP_DIR}.zip" "$BACKUP_DIR"
rm -rf "$BACKUP_DIR"

echo "💾 Backup saved to ${BACKUP_DIR}.zip"
echo "👉 You should copy this file to Google Drive or Cloud Storage for off-site safety."
