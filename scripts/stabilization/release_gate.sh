#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TS="$(date +%Y%m%dT%H%M%S%z)"
OUT_DIR="$ROOT_DIR/docs/stabilization/artifacts/release-gate-$TS"
LOG_DIR="$OUT_DIR/logs"
RESTART_OUT_DIR="$OUT_DIR/restart_gate"
BASELINE_OUT_DIR="$OUT_DIR/baseline"
DRIFT_REPORT_FILE="$OUT_DIR/drift_report.txt"
SUMMARY_FILE="$OUT_DIR/release_gate_summary.txt"
mkdir -p "$LOG_DIR"

RESTART_LOOPS="${OMEGA_RELEASE_GATE_RESTART_LOOPS:-3}"
SKIP_START="${OMEGA_RELEASE_GATE_SKIP_START:-0}"
API_URL="${OMEGA_SMOKE_API_URL:-http://127.0.0.1:8001}"
READINESS_WAIT_SECONDS="${OMEGA_RELEASE_GATE_WAIT_SECONDS:-60}"
FORCE_START="${OMEGA_RELEASE_GATE_FORCE_START:-1}"
SKIP_PREFLIGHT="${OMEGA_RELEASE_GATE_SKIP_PREFLIGHT:-1}"
STATUS="running"
FAILED_STEP=""
FAILED_LOG=""
SMOKE_SCRIPT="$ROOT_DIR/scripts/stabilization/smoke_gate.sh"
LIBRARY_INTEGRITY_SCRIPT="$ROOT_DIR/scripts/stabilization/library_integrity_gate.sh"
RESTART_SCRIPT="$ROOT_DIR/scripts/stabilization/restart_gate.sh"
BASELINE_SCRIPT="$ROOT_DIR/scripts/stabilization/capture_baseline.sh"
DRIFT_SCRIPT="$ROOT_DIR/scripts/stabilization/drift_report.sh"

write_summary() {
  {
    echo "generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "status=$STATUS"
    echo "restart_loops=$RESTART_LOOPS"
    echo "skip_start=$SKIP_START"
    echo "api_url=$API_URL"
    echo "readiness_wait_seconds=$READINESS_WAIT_SECONDS"
    echo "force_start=$FORCE_START"
    echo "skip_preflight=$SKIP_PREFLIGHT"
    echo "out_dir=$OUT_DIR"
    echo "failed_step=$FAILED_STEP"
    echo "failed_log=$FAILED_LOG"
    echo "start_log=$LOG_DIR/start.log"
    echo "readiness_log=$LOG_DIR/readiness.log"
    echo "smoke_log=$LOG_DIR/smoke.log"
    echo "library_integrity_pre_log=$LOG_DIR/library_integrity_pre.log"
    echo "library_integrity_pre_report=$OUT_DIR/library_integrity_pre.txt"
    echo "restart_gate_log=$LOG_DIR/restart_gate.log"
    echo "library_integrity_post_log=$LOG_DIR/library_integrity_post.log"
    echo "library_integrity_post_report=$OUT_DIR/library_integrity_post.txt"
    echo "baseline_log=$LOG_DIR/baseline.log"
    echo "drift_log=$LOG_DIR/drift.log"
    echo "restart_gate_dir=$RESTART_OUT_DIR"
    echo "restart_gate_summary=$RESTART_OUT_DIR/gate_summary.txt"
    echo "baseline_dir=$BASELINE_OUT_DIR"
    echo "drift_report=$DRIFT_REPORT_FILE"
  } >"$SUMMARY_FILE"
}

trap write_summary EXIT

run_step() {
  local step="$1"
  local log="$2"
  shift 2
  {
    echo "[step] $step"
    "$@"
  } >"$log" 2>&1 || {
    STATUS="failed"
    FAILED_STEP="$step"
    FAILED_LOG="$log"
    echo "Release gate failed at step: $step"
    echo "See log: $log"
    exit 1
  }
}

wait_for_api_readiness() {
  local elapsed=0
  local health_code
  local programs_code
  while true; do
    health_code="$(curl -sS -m 4 -o /dev/null -w "%{http_code}" "$API_URL/api/health" 2>/dev/null || true)"
    programs_code="$(curl -sS -m 6 -o /dev/null -w "%{http_code}" "$API_URL/api/v2/programs" 2>/dev/null || true)"
    if [ "$health_code" = "200" ] && [ "$programs_code" = "200" ]; then
      return 0
    fi
    if [ "$elapsed" -ge "$READINESS_WAIT_SECONDS" ]; then
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
}

for dep in "$SMOKE_SCRIPT" "$LIBRARY_INTEGRITY_SCRIPT" "$RESTART_SCRIPT" "$BASELINE_SCRIPT" "$DRIFT_SCRIPT"; do
  if [ ! -x "$dep" ]; then
    echo "Missing executable stabilization script: $dep"
    exit 1
  fi
done

echo "Release gate starting..."
echo "out_dir=$OUT_DIR"
echo "restart_loops=$RESTART_LOOPS"
echo "skip_start=$SKIP_START"
echo "force_start=$FORCE_START"
echo "skip_preflight=$SKIP_PREFLIGHT"

if [ "$SKIP_START" != "1" ]; then
  run_step "start_omega" "$LOG_DIR/start.log" /bin/zsh -lc "OMEGA_FORCE_START=$FORCE_START OMEGA_SKIP_PREFLIGHT=$SKIP_PREFLIGHT OMEGA_NO_TAIL=1 '$ROOT_DIR/start_omega.sh'"
else
  {
    echo "[step] start_omega"
    echo "skipped=1"
  } >"$LOG_DIR/start.log"
fi

{
  echo "[step] readiness_wait"
  echo "api_url=$API_URL"
  echo "wait_seconds=$READINESS_WAIT_SECONDS"
  if wait_for_api_readiness; then
    echo "ready=1"
  else
    echo "ready=0"
    exit 1
  fi
} >"$LOG_DIR/readiness.log" 2>&1 || {
  STATUS="failed"
  FAILED_STEP="readiness_wait"
  FAILED_LOG="$LOG_DIR/readiness.log"
  echo "Release gate failed at step: readiness_wait"
  echo "See log: $LOG_DIR/readiness.log"
  exit 1
}

run_step "smoke_gate" "$LOG_DIR/smoke.log" "$SMOKE_SCRIPT"
run_step "library_integrity_pre" "$LOG_DIR/library_integrity_pre.log" /bin/zsh -lc "OMEGA_LIBRARY_REPORT_FILE='$OUT_DIR/library_integrity_pre.txt' '$LIBRARY_INTEGRITY_SCRIPT'"
run_step "restart_gate" "$LOG_DIR/restart_gate.log" /bin/zsh -lc "OMEGA_RESTART_LOOPS=$RESTART_LOOPS OMEGA_RESTART_FORCE_START=$FORCE_START OMEGA_RESTART_SKIP_PREFLIGHT=$SKIP_PREFLIGHT OMEGA_RESTART_OUT_DIR='$RESTART_OUT_DIR' '$RESTART_SCRIPT'"
run_step "library_integrity_post" "$LOG_DIR/library_integrity_post.log" /bin/zsh -lc "OMEGA_LIBRARY_REPORT_FILE='$OUT_DIR/library_integrity_post.txt' '$LIBRARY_INTEGRITY_SCRIPT'"
run_step "capture_baseline" "$LOG_DIR/baseline.log" /bin/zsh -lc "OMEGA_BASELINE_OUT_DIR='$BASELINE_OUT_DIR' '$BASELINE_SCRIPT'"
run_step "drift_report" "$LOG_DIR/drift.log" "$DRIFT_SCRIPT" "$DRIFT_REPORT_FILE"

STATUS="passed"
echo "Release gate PASSED."
echo "Artifacts: $OUT_DIR"
