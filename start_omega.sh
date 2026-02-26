#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"

echo "🚀 Starting OmegaTV System..."

if [ -x "./scripts/rotate_logs.sh" ]; then
  ./scripts/rotate_logs.sh >/dev/null 2>&1 || true
fi

# Optional local secrets/config.
if [ -f ".omega_secrets" ]; then
  # shellcheck disable=SC1091
  source ".omega_secrets"
fi
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

# Canonical runtime defaults (single system).
export DB_TYPE="${DB_TYPE:-postgres}"
export OMEGA_CLOUD_PIPELINE="${OMEGA_CLOUD_PIPELINE:-1}"
export OMEGA_CLOUD_SYNC_ENABLED="${OMEGA_CLOUD_SYNC_ENABLED:-1}"
export OMEGA_CLOUD_RUN_JOB="${OMEGA_CLOUD_RUN_JOB:-omega-cloud-worker}"
export OMEGA_CLOUD_RUN_REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
export OMEGA_CLOUD_PROJECT="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
export OMEGA_JOBS_BUCKET="${OMEGA_JOBS_BUCKET:-omega-jobs-subtitle-project}"
export OMEGA_JOBS_PREFIX="${OMEGA_JOBS_PREFIX:-jobs}"
export OMEGA_CLOUD_MUSIC_DETECT="${OMEGA_CLOUD_MUSIC_DETECT:-1}"

export OMEGA_FASTAPI_HOST="${OMEGA_FASTAPI_HOST:-127.0.0.1}"
export OMEGA_FASTAPI_PORT="${OMEGA_FASTAPI_PORT:-8001}"
export OMEGA_FRONTEND_PORT="${OMEGA_FRONTEND_PORT:-3000}"
export OMEGA_FRONTEND_ENABLED="${OMEGA_FRONTEND_ENABLED:-1}"
export OMEGA_CORS_ORIGINS="${OMEGA_CORS_ORIGINS:-http://127.0.0.1:${OMEGA_FRONTEND_PORT},http://localhost:${OMEGA_FRONTEND_PORT}}"

# Normalize DB env names so sync + async DB clients use the same target.
export DB_HOST="${DB_HOST:-${OMEGA_PG_HOST:-127.0.0.1}}"
export DB_PORT="${DB_PORT:-${OMEGA_PG_PORT:-5432}}"
export DB_NAME="${DB_NAME:-${OMEGA_PG_DB:-postgres}}"
if [ -z "${DB_USER:-}" ] && [ -n "${OMEGA_PG_USER:-}" ]; then export DB_USER="${OMEGA_PG_USER}"; fi
if [ -z "${DB_PASS:-}" ] && [ -n "${OMEGA_PG_PASS:-}" ]; then export DB_PASS="${OMEGA_PG_PASS}"; fi

# Pick Python once and pass to PM2 ecosystem.
python_has_runtime_deps() {
  local py="$1"
  "$py" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys
required = ("fastapi", "uvicorn", "vertexai")
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(0 if not missing else 1)
PY
}

pick_python() {
  local candidate
  local fallback=""
  local candidates=()

  if [ -n "${OMEGA_PYTHON:-}" ]; then candidates+=("${OMEGA_PYTHON}"); fi
  if [ -n "${OMEGA_VENV_PY:-}" ]; then candidates+=("${OMEGA_VENV_PY}"); fi

  candidates+=(
    "$BASE_DIR/.venv/bin/python3"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "/usr/local/bin/python3.12"
    "/usr/local/bin/python3.11"
    "/usr/local/bin/python3.10"
    "/usr/bin/python3"
  )

  for candidate in "${candidates[@]}"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      if [ -z "$fallback" ]; then fallback="$candidate"; fi
      if python_has_runtime_deps "$candidate"; then
        echo "$candidate"
        return 0
      fi
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    candidate="$(command -v python3)"
    if [ -z "$fallback" ]; then fallback="$candidate"; fi
    if python_has_runtime_deps "$candidate"; then
      echo "$candidate"
      return 0
    fi
  fi

  if [ -n "$fallback" ]; then
    echo "$fallback"
    return 0
  fi

  return 1
}

if ! OMEGA_PYTHON="$(pick_python)"; then
  echo "🛑 Python 3 interpreter not found."
  exit 1
fi
export OMEGA_PYTHON
echo "🐍 Python runtime: $OMEGA_PYTHON ($("$OMEGA_PYTHON" -V 2>&1))"
if ! python_has_runtime_deps "$OMEGA_PYTHON"; then
  echo "🛑 Selected Python runtime is missing required runtime deps (fastapi/uvicorn/vertexai): $OMEGA_PYTHON"
  echo "   Install with: $OMEGA_PYTHON -m pip install -r requirements.runtime.txt"
  exit 1
fi
if "$OMEGA_PYTHON" - <<'PY' >/dev/null 2>&1
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  :
else
  echo "⚠️  Python < 3.10 detected. System can run, but upgrade to 3.11+ is recommended."
fi

# Resolve PM2 (global first, then local project installs).
PM2_BIN="${OMEGA_PM2_BIN:-}"
if [ -n "$PM2_BIN" ] && [ ! -x "$PM2_BIN" ]; then
  echo "🛑 OMEGA_PM2_BIN points to a non-executable path: $PM2_BIN"
  exit 1
fi
if [ -z "$PM2_BIN" ] && command -v pm2 >/dev/null 2>&1; then
  PM2_BIN="$(command -v pm2)"
fi
if [ -z "$PM2_BIN" ] && [ -x "$BASE_DIR/node_modules/.bin/pm2" ]; then
  PM2_BIN="$BASE_DIR/node_modules/.bin/pm2"
fi
if [ -z "$PM2_BIN" ] && [ -x "$BASE_DIR/omega-frontend/node_modules/.bin/pm2" ]; then
  PM2_BIN="$BASE_DIR/omega-frontend/node_modules/.bin/pm2"
fi
if [ -z "$PM2_BIN" ]; then
  echo "🛑 PM2 not found."
  echo "   Install with ./scripts/install_pm2.sh (or npm i -g pm2)"
  exit 1
fi
echo "🔧 PM2 runtime: $PM2_BIN"

# Frontend contract checks (fail fast)
validate_frontend_env_contract() {
  local env_file="$BASE_DIR/omega-frontend/.env.local"
  local api_url=""
  local socket_url=""
  local allow_api_override="${OMEGA_ALLOW_FRONTEND_API_OVERRIDE:-0}"
  local allow_socket_override="${OMEGA_ALLOW_FRONTEND_SOCKET_OVERRIDE:-0}"

  if [ -f "$env_file" ]; then
    api_url="$(grep -E '^NEXT_PUBLIC_API_URL=' "$env_file" | tail -n 1 | cut -d= -f2- || true)"
    socket_url="$(grep -E '^NEXT_PUBLIC_SOCKET_URL=' "$env_file" | tail -n 1 | cut -d= -f2- || true)"
  fi

  if [ -n "$api_url" ] && [ "$allow_api_override" != "1" ]; then
    echo "🛑 Frontend env contract violation: NEXT_PUBLIC_API_URL must be empty in omega-frontend/.env.local."
    echo "   Found: $api_url"
    echo "   To allow explicit override, set OMEGA_ALLOW_FRONTEND_API_OVERRIDE=1."
    exit 1
  fi

  if [ -n "$api_url" ] && [ "$allow_api_override" = "1" ]; then
    case "$api_url" in
      "http://127.0.0.1:${OMEGA_FASTAPI_PORT}"|"http://localhost:${OMEGA_FASTAPI_PORT}")
        ;;
      *)
        echo "🛑 Frontend env contract violation: unsupported NEXT_PUBLIC_API_URL override '$api_url'."
        echo "   Allowed local overrides:"
        echo "   - http://127.0.0.1:${OMEGA_FASTAPI_PORT}"
        echo "   - http://localhost:${OMEGA_FASTAPI_PORT}"
        exit 1
        ;;
    esac
  fi

  if [ -n "$socket_url" ] && [ "$allow_socket_override" != "1" ]; then
    echo "🛑 Frontend env contract violation: NEXT_PUBLIC_SOCKET_URL must be empty in omega-frontend/.env.local."
    echo "   Found: $socket_url"
    echo "   To allow explicit override, set OMEGA_ALLOW_FRONTEND_SOCKET_OVERRIDE=1."
    exit 1
  fi
}

validate_frontend_build_contract() {
  local frontend_dir="$BASE_DIR/omega-frontend"
  if [ "${OMEGA_FRONTEND_ENABLED:-1}" != "1" ]; then
    return 0
  fi
  if [ ! -d "$frontend_dir" ]; then
    echo "🛑 Frontend directory missing: $frontend_dir"
    exit 1
  fi
  if [ ! -f "$frontend_dir/.next/standalone/server.js" ]; then
    echo "🛑 Frontend build contract violation: missing .next/standalone/server.js"
    echo "   Build required: cd omega-frontend && npm run build"
    exit 1
  fi
  if [ ! -d "$frontend_dir/.next/static" ]; then
    echo "🛑 Frontend build contract violation: missing .next/static"
    echo "   Build required: cd omega-frontend && npm run build"
    exit 1
  fi
}

validate_frontend_env_contract
validate_frontend_build_contract

# Ensure clean slate unless explicitly skipped.
if [ "${OMEGA_SKIP_STOP:-0}" != "1" ]; then
  ./stop_all.sh
fi

# Validate storage readiness.
if [ "${OMEGA_LOCAL_TEST:-0}" != "1" ]; then
  DRIVE_PATH="${OMEGA_DRIVE_PATH:-/Volumes/Extreme SSD}"
  WAIT_SECONDS="${OMEGA_DRIVE_WAIT_SECONDS:-120}"
  STEP_SECONDS=5
  ELAPSED=0
  echo "💾 Checking Storage: $DRIVE_PATH"
  until [ -d "$DRIVE_PATH" ]; do
    if [ "$ELAPSED" -ge "$WAIT_SECONDS" ]; then
      echo "🛑 Storage not mounted after ${WAIT_SECONDS}s: $DRIVE_PATH"
      exit 1
    fi
    echo "⚠️  Drive not found yet, waiting ${STEP_SECONDS}s... (${ELAPSED}/${WAIT_SECONDS}s)"
    sleep "$STEP_SECONDS"
    ELAPSED=$((ELAPSED + STEP_SECONDS))
  done
fi

if ! "$OMEGA_PYTHON" - <<'PY'
import sys
import config
sys.exit(0 if config.critical_paths_ready(require_write=True) else 1)
PY
then
  echo "🛑 Critical paths not writable/ready. Aborting startup."
  exit 1
fi
echo "✅ Storage and paths ready"

# Pre-flight validation.
if [ "${OMEGA_SKIP_PREFLIGHT:-0}" = "1" ]; then
  echo "⏭️  Skipping pre-flight checks (OMEGA_SKIP_PREFLIGHT=1)."
else
  PREFLIGHT_RESULT=0
  if [ -f "preflight_check.py" ]; then
    "$OMEGA_PYTHON" preflight_check.py || PREFLIGHT_RESULT=$?
  elif [ -f "preflight.py" ]; then
    "$OMEGA_PYTHON" preflight.py || PREFLIGHT_RESULT=$?
  fi
  if [ "$PREFLIGHT_RESULT" -eq 1 ] && [ "${OMEGA_FORCE_START:-0}" != "1" ]; then
    echo "🛑 Pre-flight failed. Set OMEGA_FORCE_START=1 to override."
    exit 1
  fi
fi

echo "⚙️  Starting unified stack with PM2..."
OMEGA_PYTHON="$OMEGA_PYTHON" "$PM2_BIN" start ecosystem.config.js --update-env >/dev/null
"$PM2_BIN" save >/dev/null 2>&1 || true

"$PM2_BIN" status
echo "✅ Omega stack running under PM2."
echo "   FastAPI:  http://${OMEGA_FASTAPI_HOST}:${OMEGA_FASTAPI_PORT}"
echo "   Frontend: http://127.0.0.1:${OMEGA_FRONTEND_PORT}"

if [ "${OMEGA_NO_TAIL:-0}" = "1" ]; then
  exit 0
fi

echo "📜 Streaming PM2 logs (Ctrl+C to detach; services keep running)..."
"$PM2_BIN" logs --lines 80
