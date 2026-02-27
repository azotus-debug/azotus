#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🛑 Stopping all OmegaTV processes..."

PM2_APPS=(
  "omega-cloud-sql-proxy"
  "omega-fastapi"
  "omega-manager"
  "omega-cloud-sync"
  "omega-frontend"
)

PM2_BIN="${OMEGA_PM2_BIN:-}"
if [ -z "$PM2_BIN" ] && command -v pm2 >/dev/null 2>&1; then
  PM2_BIN="$(command -v pm2)"
fi
if [ -z "$PM2_BIN" ] && [ -x "$BASE_DIR/node_modules/.bin/pm2" ]; then
  PM2_BIN="$BASE_DIR/node_modules/.bin/pm2"
fi
if [ -z "$PM2_BIN" ] && [ -x "$BASE_DIR/omega-frontend/node_modules/.bin/pm2" ]; then
  PM2_BIN="$BASE_DIR/omega-frontend/node_modules/.bin/pm2"
fi

if [ -n "$PM2_BIN" ] && [ -x "$PM2_BIN" ]; then
  for app in "${PM2_APPS[@]}"; do
    "$PM2_BIN" delete "$app" >/dev/null 2>&1 || true
  done
fi

# Residual cleanup in case an old/manual process survived PM2 teardown.
pkill -f "uvicorn api_main:socket_app" >/dev/null 2>&1 || true
pkill -f "omega_manager.py" >/dev/null 2>&1 || true
pkill -f "cloud_sync_service.py" >/dev/null 2>&1 || true
pkill -f "cloud-sql-proxy .*omega-sql-prod" >/dev/null 2>&1 || true
pkill -f "node .next/standalone/server.js" >/dev/null 2>&1 || true
pkill -f "next-server" >/dev/null 2>&1 || true
pkill -f "omega-frontend/node_modules/.bin/next" >/dev/null 2>&1 || true
pkill -f "caffeinate -dimsu -w" >/dev/null 2>&1 || true

rm -f /tmp/omega_manager.lock \
      /tmp/omega_cloud_sql_proxy.pid \
      /tmp/omega_fastapi.pid \
      /tmp/omega_frontend.pid \
      /tmp/omega_cloud_sync.pid \
      /tmp/omega_caffeinate.pid

echo "✅ All processes stopped."
