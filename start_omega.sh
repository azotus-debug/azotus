#!/bin/bash

# Omega Manager Launcher
# Ensures the process starts in the background without being suspended by SIGTTOU.

# Ensure clean slate
if [ "${OMEGA_SKIP_STOP:-0}" != "1" ]; then
  ./stop_all.sh
fi

LOG_FILE="logs/manager.log"
LOCK_FILE="/tmp/omega_manager.lock"
WATCHDOG_PID_FILE="/tmp/omega_watchdog.pid"
WATCHDOG_SUP_PID_FILE="/tmp/omega_watchdog_supervisor.pid"
CAFFEINATE_PID_FILE="/tmp/omega_caffeinate.pid"
CLOUD_SYNC_PID_FILE="/tmp/omega_cloud_sync.pid"
FRONTEND_PID_FILE="/tmp/omega_frontend.pid"

echo "🚀 Starting OmegaTV System..."

# Ensure logs folder exists
mkdir -p logs

# Rotate logs if they're too large (prevents disk filling up)
if [ -x "./scripts/rotate_logs.sh" ]; then
  ./scripts/rotate_logs.sh > /dev/null 2>&1
fi

# Optional: load secrets from a local file (kept out of git).
if [ -f ".omega_secrets" ]; then
  # shellcheck disable=SC1091
  source ".omega_secrets"
fi

# Load .env file (for OMEGA_TRANSCRIBER and other settings)
if [ -f ".env" ]; then
  set -a  # automatically export all variables
  source ".env"
  set +a
fi

# Cloud Run Job defaults (override in shell if needed)
export OMEGA_CLOUD_RUN_JOB="${OMEGA_CLOUD_RUN_JOB:-omega-cloud-worker}"
export OMEGA_CLOUD_RUN_REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
export OMEGA_CLOUD_PROJECT="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
# Cloud-first translation/editor pipeline (disable by setting OMEGA_CLOUD_PIPELINE=0)
export OMEGA_CLOUD_PIPELINE="${OMEGA_CLOUD_PIPELINE:-1}"
export OMEGA_JOBS_BUCKET="${OMEGA_JOBS_BUCKET:-omega-jobs-subtitle-project}"
export OMEGA_JOBS_PREFIX="${OMEGA_JOBS_PREFIX:-jobs}"
# Enable the 3rd-pass polish step for all jobs by default ("review" or "all").
# Detect and suppress choir/worship lyrics before translation.
export OMEGA_CLOUD_MUSIC_DETECT="${OMEGA_CLOUD_MUSIC_DETECT:-1}"
# Cloud sync daemon (poll GCS for completed jobs)
export OMEGA_CLOUD_SYNC_ENABLED="${OMEGA_CLOUD_SYNC_ENABLED:-1}"
export OMEGA_CLOUD_SYNC_POLL_SECONDS="${OMEGA_CLOUD_SYNC_POLL_SECONDS:-60}"
export OMEGA_CLOUD_SYNC_BATCH_LIMIT="${OMEGA_CLOUD_SYNC_BATCH_LIMIT:-50}"
export OMEGA_CLOUD_SYNC_DB_FAILURE_THRESHOLD="${OMEGA_CLOUD_SYNC_DB_FAILURE_THRESHOLD:-3}"
export OMEGA_CLOUD_SYNC_DB_COOLDOWN_SECONDS="${OMEGA_CLOUD_SYNC_DB_COOLDOWN_SECONDS:-300}"
export DB_TYPE="${DB_TYPE:-postgres}"
# Remote review portal (Cloud Run URL) + email settings
export OMEGA_REVIEW_PORTAL_URL="${OMEGA_REVIEW_PORTAL_URL:-}"
export OMEGA_REVIEWER_EMAIL="${OMEGA_REVIEWER_EMAIL:-hawk1982@me.com}"
export OMEGA_SMTP_HOST="${OMEGA_SMTP_HOST:-smtp.gmail.com}"
export OMEGA_SMTP_PORT="${OMEGA_SMTP_PORT:-587}"
export OMEGA_SMTP_USER="${OMEGA_SMTP_USER:-haukur1982@gmail.com}"
export OMEGA_SMTP_PASS="${OMEGA_SMTP_PASS:-}"
export OMEGA_SMTP_FROM="${OMEGA_SMTP_FROM:-haukur1982@gmail.com}"
# Frontend (Next.js)
export OMEGA_FRONTEND_ENABLED="${OMEGA_FRONTEND_ENABLED:-1}"
export OMEGA_FRONTEND_URL="${OMEGA_FRONTEND_URL:-http://127.0.0.1:3000}"
# Allow PyTorch to load trusted model checkpoints when needed.
export TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="${TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD:-1}"

# --- TRANSCRIPTION (ElevenLabs Scribe v2) ---
# API key loaded from .omega_secrets or .env
export ELEVENLABS_API_KEY="${ELEVENLABS_API_KEY:-}"
# Transcriber backend: ElevenLabs only
export OMEGA_TRANSCRIBER="${OMEGA_TRANSCRIBER:-elevenlabs}"

# --- SUBTITLE TIMING CONTROLS (finalizer) ---
# Modes: balanced (default, readability-first) or strict (tight sync, minimal extension)
# export OMEGA_TIMING_MODE="${OMEGA_TIMING_MODE:-balanced}"
# In strict mode, allow a small tail after last word (seconds)
# export OMEGA_TIMING_STRICT_MAX_EXTEND="${OMEGA_TIMING_STRICT_MAX_EXTEND:-0.15}"
# In strict mode, optional fallback shift when fragment timing is missing (seconds)
# export OMEGA_TIMING_STRICT_FRAGMENT_SHIFT="${OMEGA_TIMING_STRICT_FRAGMENT_SHIFT:-0.0}"

# Pick Python (prefer venv if present)
BASE_DIR="$(pwd)"
if [ -n "${OMEGA_VENV_PY:-}" ] && [ -x "${OMEGA_VENV_PY:-}" ]; then
  OMEGA_PYTHON="${OMEGA_VENV_PY}"
elif [ -x "$BASE_DIR/.venv/bin/python3" ]; then
  OMEGA_PYTHON="$BASE_DIR/.venv/bin/python3"
else
  OMEGA_PYTHON="python3"
fi
export OMEGA_PYTHON

# Ensure user-installed Python CLI tools are on PATH
PY_USER_BIN="$($OMEGA_PYTHON -m site --user-base 2>/dev/null)/bin"
if [ -d "$PY_USER_BIN" ]; then
  export PATH="$PY_USER_BIN:$PATH"
fi

# Review Portal Configuration
export OMEGA_REVIEW_PORTAL_ENABLED="${OMEGA_REVIEW_PORTAL_ENABLED:-0}"
export OMEGA_REVIEW_PORTAL_URL="${OMEGA_REVIEW_PORTAL_URL:-https://omega-review-283123700702.us-central1.run.app}"
export OMEGA_REVIEW_SECRET="${OMEGA_REVIEW_SECRET:-omega-review-secret-2024}"

# 1. Check if already running
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE")
    if ps -p $PID > /dev/null; then
        echo "❌ Manager is already running (PID: $PID)"
        exit 1
    else
        echo "⚠️ Found stale lock file. Cleaning up..."
        rm "$LOCK_FILE"
    fi
fi

# 2. Start Dashboard
echo "📊 Starting Dashboard..."
nohup "$OMEGA_PYTHON" dashboard.py > logs/dashboard.log 2>&1 &
DASH_PID=$!
echo "   Dashboard PID: $DASH_PID"
echo "   Backend API: http://127.0.0.1:8080"

# 2.1 Start Frontend (Next.js)
FRONTEND_ENABLED=$(echo "${OMEGA_FRONTEND_ENABLED}" | tr '[:upper:]' '[:lower:]')
if [ "$FRONTEND_ENABLED" = "1" ] || [ "$FRONTEND_ENABLED" = "true" ] || [ "$FRONTEND_ENABLED" = "yes" ] || [ "$FRONTEND_ENABLED" = "on" ]; then
  # Clean up orphaned frontend processes from previous runs to avoid multi-port drift.
  FRONTEND_PATH_PATTERN="$BASE_DIR/omega-frontend/node_modules/.bin/next"
  ORPHAN_FRONTEND_PIDS=$(ps aux | grep "$FRONTEND_PATH_PATTERN" | awk '{print $2}')
  if [ -n "$ORPHAN_FRONTEND_PIDS" ]; then
    echo "🧹 Cleaning orphaned Frontend PIDs: $ORPHAN_FRONTEND_PIDS"
    kill -9 $ORPHAN_FRONTEND_PIDS 2>/dev/null
    rm -f "$FRONTEND_PID_FILE"
    sleep 1
  fi
  NEXT_SERVER_PIDS=$(ps aux | awk '/[n]ext-server/{print $2}')
  ORPHAN_NEXT_SERVER_PIDS=""
  for pid in $NEXT_SERVER_PIDS; do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)
    case "$cwd" in
      */Azotus/omega-frontend*)
        ORPHAN_NEXT_SERVER_PIDS="$ORPHAN_NEXT_SERVER_PIDS $pid"
        ;;
    esac
  done
  if [ -n "$ORPHAN_NEXT_SERVER_PIDS" ]; then
    echo "🧹 Cleaning orphaned next-server PIDs:$ORPHAN_NEXT_SERVER_PIDS"
    kill -9 $ORPHAN_NEXT_SERVER_PIDS 2>/dev/null
    rm -f "$FRONTEND_PID_FILE"
    sleep 1
  fi

  if [ -f "$FRONTEND_PID_FILE" ]; then
    OLD_FRONTEND_PID=$(cat "$FRONTEND_PID_FILE" 2>/dev/null)
    if [ -n "$OLD_FRONTEND_PID" ] && kill -0 "$OLD_FRONTEND_PID" 2>/dev/null; then
      echo "🌐 Frontend already running (PID: $OLD_FRONTEND_PID)"
    else
      rm -f "$FRONTEND_PID_FILE"
    fi
  fi

  if [ ! -f "$FRONTEND_PID_FILE" ]; then
    if command -v npm >/dev/null 2>&1; then
      FRONTEND_DIR="$BASE_DIR/omega-frontend"
      if [ -d "$FRONTEND_DIR" ]; then
        if [ -f "$FRONTEND_DIR/.next/BUILD_ID" ]; then
          echo "🌐 Starting Frontend (Next.js - production)..."
          nohup npm --prefix "$FRONTEND_DIR" run start > logs/frontend.log 2>&1 &
        else
          echo "🌐 Starting Frontend (Next.js - dev)..."
          nohup npm --prefix "$FRONTEND_DIR" run dev > logs/frontend.log 2>&1 &
        fi
        FRONTEND_PID=$!
        echo $FRONTEND_PID > "$FRONTEND_PID_FILE"
        echo "   Frontend PID: $FRONTEND_PID"
        echo "   Frontend: $OMEGA_FRONTEND_URL"
      else
        echo "⚠️ Frontend directory missing: $FRONTEND_DIR"
      fi
    else
      echo "⚠️ npm not found. Frontend not started."
    fi
  fi
fi

# 2.5 Check external storage readiness (symlink targets writable)
# Skip SSD check if OMEGA_LOCAL_TEST=1 (for testing without external drive)
if [ "${OMEGA_LOCAL_TEST:-0}" = "1" ]; then
  echo "💾 LOCAL TEST MODE - skipping SSD check"
  DRIVE_PATH="$HOME/OmegaTest"
else
  DRIVE_PATH="${OMEGA_DRIVE_PATH:-/Volumes/Extreme SSD}"
fi
echo "💾 Checking Storage: $DRIVE_PATH"

# Wait Loop
retries=0
while [ ! -d "$DRIVE_PATH" ]; do
    echo "⚠️  WARNING: Drive not found! Waiting 10s... (Attempt $((retries+1)))"
    sleep 10
    retries=$((retries+1))
    
    # Alert at 2 minutes (12 * 10s = 120s)
    if [ $retries -eq 12 ]; then
        echo "🚨 ALERT: Critical Drive Failure (2 mins). Sending notification..."
        "$OMEGA_PYTHON" scripts/check_drive_alert.py 2
    fi
    # Re-alert at 10 minutes
    if [ $retries -eq 60 ]; then
        "$OMEGA_PYTHON" scripts/check_drive_alert.py 10
    fi
done

echo "   ✅ Drive Mounted. Verifying configuration..."
if "$OMEGA_PYTHON" - <<'PY'
import sys
import config
sys.exit(0 if config.critical_paths_ready(require_write=True) else 1)
PY
then
  echo "   ✅ Storage and Paths ready"
else
  echo "   ❌ Config validation failed despite drive presence. Proceeding with caution."
fi

# 2.7 Start Cloud SQL Auth Proxy (if using PostgreSQL)
CLOUD_SQL_PROXY_PID_FILE="/tmp/omega_cloud_sql_proxy.pid"
if [ "${DB_TYPE:-postgres}" = "postgres" ]; then
  echo "🗄️  PostgreSQL mode enabled. Starting Cloud SQL Auth Proxy..."

  # Check if proxy is already running
  if [ -f "$CLOUD_SQL_PROXY_PID_FILE" ]; then
    OLD_PROXY_PID=$(cat "$CLOUD_SQL_PROXY_PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PROXY_PID" ] && kill -0 "$OLD_PROXY_PID" 2>/dev/null; then
      echo "   ✅ Cloud SQL Proxy already running (PID: $OLD_PROXY_PID)"
    else
      rm -f "$CLOUD_SQL_PROXY_PID_FILE"
    fi
  fi

  # Start proxy if not running
  if [ ! -f "$CLOUD_SQL_PROXY_PID_FILE" ]; then
    if command -v cloud-sql-proxy >/dev/null 2>&1; then
      INSTANCE="${OMEGA_CLOUD_SQL_INSTANCE:-sermon-translator-system:us-central1:omega-sql-prod}"
      nohup cloud-sql-proxy "$INSTANCE" --port="${OMEGA_PG_PORT:-5432}" > logs/cloud_sql_proxy.log 2>&1 &
      PROXY_PID=$!
      echo $PROXY_PID > "$CLOUD_SQL_PROXY_PID_FILE"
      echo "   Cloud SQL Proxy started (PID: $PROXY_PID)"

      # Wait for proxy to be ready (max 10 seconds)
      echo "   Waiting for proxy to be ready..."
      for i in $(seq 1 10); do
        if nc -z 127.0.0.1 "${OMEGA_PG_PORT:-5432}" 2>/dev/null; then
          echo "   ✅ Cloud SQL Proxy ready"
          break
        fi
        sleep 1
        if [ $i -eq 10 ]; then
          echo "   ⚠️  Cloud SQL Proxy may not be ready yet, proceeding anyway"
        fi
      done
    else
      echo "   ❌ cloud-sql-proxy not found! Install with: brew install cloud-sql-proxy"
      echo "   Falling back to direct connection (may fail if not on Cloud Run)"
    fi
  fi
fi

# 2.8 Pre-Flight System Check (validates GCS, Vertex, FFmpeg, etc.)
echo "🔍 Running Comprehensive Pre-Flight Check..."
PREFLIGHT_RESULT=0
if [ -f "preflight_check.py" ]; then
  "$OMEGA_PYTHON" preflight_check.py
  PREFLIGHT_RESULT=$?
else
  # Fallback to old preflight.py
  "$OMEGA_PYTHON" preflight.py
  PREFLIGHT_RESULT=$?
fi

if [ $PREFLIGHT_RESULT -eq 0 ]; then
  echo "   ✅ All systems operational"
elif [ $PREFLIGHT_RESULT -eq 2 ]; then
  echo ""
  echo "   ⚠️  Pre-flight check found warnings. Review above."
  echo "   System will start, but some features may not work optimally."
  echo ""
elif [ $PREFLIGHT_RESULT -eq 1 ]; then
  echo ""
  echo "   🛑 CRITICAL: Pre-flight check failed!"
  echo "   Fix the issues above before starting the system."
  echo ""
  # Allow override with OMEGA_FORCE_START=1
  if [ "${OMEGA_FORCE_START:-0}" = "1" ]; then
    echo "   ⚠️  OMEGA_FORCE_START=1 set - proceeding despite failures..."
  else
    echo "   Set OMEGA_FORCE_START=1 to override (not recommended)"
    # Kill dashboard (and frontend if running)
    kill $DASH_PID 2>/dev/null
    if [ -f "$FRONTEND_PID_FILE" ]; then
      FRONTEND_PID=$(cat "$FRONTEND_PID_FILE" 2>/dev/null)
      if [ -n "$FRONTEND_PID" ]; then
        kill "$FRONTEND_PID" 2>/dev/null
      fi
      rm -f "$FRONTEND_PID_FILE"
    fi
    exit 1
  fi
fi

# 3. Start Manager in background
nohup "$OMEGA_PYTHON" omega_manager.py > /dev/null 2>&1 &

# 3. Capture PID
NEW_PID=$!
echo "✅ Manager started with PID: $NEW_PID"

# 3.1 Start Cloud Sync daemon (optional)
SYNC_ENABLED=$(echo "${OMEGA_CLOUD_SYNC_ENABLED}" | tr '[:upper:]' '[:lower:]')
if [ "$SYNC_ENABLED" = "1" ] || [ "$SYNC_ENABLED" = "true" ] || [ "$SYNC_ENABLED" = "yes" ] || [ "$SYNC_ENABLED" = "on" ]; then
  nohup "$OMEGA_PYTHON" cloud_sync_service.py > logs/cloud_sync.log 2>&1 &
  CLOUD_SYNC_PID=$!
  echo $CLOUD_SYNC_PID > "$CLOUD_SYNC_PID_FILE"
  echo "☁️ Cloud Sync started with PID: $CLOUD_SYNC_PID"
fi

# 3.5 Keep Mac awake while manager runs (prevents sleep stalls)
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -dimsu -w "$NEW_PID" >/dev/null 2>&1 &
  echo $! > "$CAFFEINATE_PID_FILE"
  echo "☕ Caffeinate active (PID: $(cat "$CAFFEINATE_PID_FILE"))"
fi

# 3.6 Start watchdog supervisor (keeps watchdog alive)
if [ -f "$WATCHDOG_SUP_PID_FILE" ]; then
  OLD_SUP_PID=$(cat "$WATCHDOG_SUP_PID_FILE" 2>/dev/null)
  if [ -n "$OLD_SUP_PID" ] && kill -0 "$OLD_SUP_PID" 2>/dev/null; then
    echo "🐶 Watchdog supervisor already running (PID: $OLD_SUP_PID)"
  else
    rm -f "$WATCHDOG_SUP_PID_FILE"
  fi
fi
if [ ! -f "$WATCHDOG_SUP_PID_FILE" ]; then
  nohup "$OMEGA_PYTHON" watchdog_supervisor.py > logs/watchdog_supervisor.log 2>&1 &
  echo $! > "$WATCHDOG_SUP_PID_FILE"
  echo "🐶 Watchdog supervisor started (PID: $(cat "$WATCHDOG_SUP_PID_FILE"))"
fi

# 4. Tail the log file so user sees immediate feedback
if [ "${OMEGA_NO_TAIL:-0}" = "1" ]; then
  exit 0
fi
echo "📜 Tailing logs (Ctrl+C to exit tail, Manager will keep running)..."
echo "----------------------------------------------------------------"
tail -f "$LOG_FILE"
