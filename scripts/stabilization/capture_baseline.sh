#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TS="$(date +%Y%m%dT%H%M%S%z)"
DEFAULT_OUT_DIR="$ROOT_DIR/docs/stabilization/artifacts/$TS"
OUT_DIR="${OMEGA_BASELINE_OUT_DIR:-$DEFAULT_OUT_DIR}"
mkdir -p "$OUT_DIR"

VENV_PY="$ROOT_DIR/.venv/bin/python3"
if [ -x "$VENV_PY" ]; then
  PYTHON_BIN="$VENV_PY"
else
  PYTHON_BIN="python3"
fi

run_optional() {
  local label="$1"
  shift
  {
    echo "### ${label}"
    "$@"
  } >>"$OUT_DIR/commands.log" 2>&1 || {
    echo "### ${label}"
    echo "command_failed: $*" >>"$OUT_DIR/commands.log"
  }
}

{
  echo "timestamp=$TS"
  echo "iso_time=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "hostname=$(hostname)"
  echo "pwd=$ROOT_DIR"
} >"$OUT_DIR/metadata.txt"

run_optional "system_uname" uname -a
run_optional "system_sw_vers" sw_vers
run_optional "python_version" "$PYTHON_BIN" --version
run_optional "node_version" node --version
run_optional "npm_version" npm --version
run_optional "pm2_version" pm2 --version

{
  echo "branch=$(git -C "$ROOT_DIR" branch --show-current)"
  echo "commit=$(git -C "$ROOT_DIR" rev-parse HEAD)"
  echo "short_commit=$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
} >"$OUT_DIR/git_ref.txt"
git -C "$ROOT_DIR" status --porcelain=1 >"$OUT_DIR/git_status_porcelain.txt" || true

run_optional "process_snapshot" /bin/zsh -lc "ps aux | rg -n 'omega|uvicorn|next-server|cloud-sql-proxy|pm2' || true"
run_optional "port_snapshot" /bin/zsh -lc "lsof -nP -iTCP -sTCP:LISTEN | rg -n ':(3000|8001|5432|8080)' || true"

API_URL="${OMEGA_SMOKE_API_URL:-http://127.0.0.1:8001}"
FRONTEND_URL="${OMEGA_SMOKE_FRONTEND_URL:-http://127.0.0.1:3000}"

capture_http() {
  local name="$1"
  local url="$2"
  local body="$OUT_DIR/${name}.body"
  local meta="$OUT_DIR/${name}.meta"
  local code
  code="$(curl -sS -m 10 -o "$body" -w "%{http_code}" "$url" || true)"
  {
    echo "name=$name"
    echo "url=$url"
    echo "http_code=$code"
    echo "bytes=$(wc -c < "$body" 2>/dev/null || echo 0)"
  } >"$meta"
}

capture_http "api_health" "$API_URL/api/health"
capture_http "api_health_stuck" "$API_URL/api/v2/health/stuck"
capture_http "api_programs" "$API_URL/api/v2/programs"
capture_http "api_tracks_active" "$API_URL/api/v2/tracks/active"
capture_http "frontend_root" "$FRONTEND_URL/"
capture_http "frontend_programs" "$FRONTEND_URL/api/v2/programs"

if [ "${INCLUDE_PIPELINE_AUDIT:-0}" = "1" ] && [ -f "$ROOT_DIR/scripts/pipeline_audit.py" ]; then
  PIPELINE_AUDIT_TIMEOUT_SECONDS="${PIPELINE_AUDIT_TIMEOUT_SECONDS:-120}"
  "$PYTHON_BIN" - \
    "$PYTHON_BIN" \
    "$ROOT_DIR/scripts/pipeline_audit.py" \
    "$OUT_DIR/pipeline_audit.json" \
    "$OUT_DIR/pipeline_audit.stderr" \
    "$PIPELINE_AUDIT_TIMEOUT_SECONDS" \
    >"$OUT_DIR/pipeline_audit.meta" <<'PY'
import subprocess
import sys

python_bin, script_path, out_path, err_path, timeout_s = sys.argv[1:]
timeout = int(timeout_s)

with open(out_path, "w", encoding="utf-8") as out_f, open(err_path, "w", encoding="utf-8") as err_f:
    try:
        result = subprocess.run(
            [python_bin, script_path, "--pretty"],
            stdout=out_f,
            stderr=err_f,
            timeout=timeout,
            check=False,
        )
        print(f"pipeline_audit_exit={result.returncode}")
    except subprocess.TimeoutExpired:
        err_f.write(f"pipeline_audit_timeout={timeout}\n")
        print("pipeline_audit_exit=timeout")
PY
else
  echo "pipeline_audit_skipped=1" >"$OUT_DIR/pipeline_audit.meta"
fi

{
  echo "Baseline captured."
  echo "artifact_dir=$OUT_DIR"
  echo "api_url=$API_URL"
  echo "frontend_url=$FRONTEND_URL"
} >"$OUT_DIR/summary.txt"

echo "Baseline artifact: $OUT_DIR"
