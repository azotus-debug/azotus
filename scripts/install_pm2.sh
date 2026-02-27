#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v npm >/dev/null 2>&1; then
  echo "🛑 npm is required to install PM2."
  exit 1
fi

run_with_timeout() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 300 "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout 300 "$@"
  elif command -v /usr/bin/python3 >/dev/null 2>&1; then
    /usr/bin/python3 - "$@" <<'PY'
import subprocess
import sys

cmd = sys.argv[1:]
try:
    completed = subprocess.run(cmd, timeout=300)
    raise SystemExit(completed.returncode)
except subprocess.TimeoutExpired:
    print("🛑 Command timed out after 300 seconds", file=sys.stderr)
    raise SystemExit(124)
PY
  else
    "$@"
  fi
}

echo "📦 Installing PM2 globally..."
if run_with_timeout npm install -g pm2; then
  echo "✅ PM2 installed globally: $(pm2 -v)"
  exit 0
fi

echo "⚠️  Global install failed; trying local project install..."
if [ -f "$BASE_DIR/package.json" ]; then
  run_with_timeout npm --prefix "$BASE_DIR" install --save-dev pm2
  LOCAL_PM2="$BASE_DIR/node_modules/.bin/pm2"
elif [ -f "$BASE_DIR/omega-frontend/package.json" ]; then
  run_with_timeout npm --prefix "$BASE_DIR/omega-frontend" install --save-dev pm2
  LOCAL_PM2="$BASE_DIR/omega-frontend/node_modules/.bin/pm2"
else
  echo "🛑 No package.json found for local PM2 install."
  exit 1
fi

if [ -x "${LOCAL_PM2:-}" ]; then
  echo "✅ PM2 installed locally: $LOCAL_PM2"
  echo "   Export OMEGA_PM2_BIN=\"$LOCAL_PM2\" before running start_omega.sh"
else
  echo "🛑 PM2 install finished but binary not found."
  exit 1
fi
