#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOOPS="${OMEGA_RESTART_LOOPS:-10}"
ARTIFACT_BASE="$ROOT_DIR/docs/stabilization/artifacts"
TS="$(date +%Y%m%dT%H%M%S%z)"
DEFAULT_OUT_DIR="$ARTIFACT_BASE/restart-gate-$TS"
OUT_DIR="${OMEGA_RESTART_OUT_DIR:-$DEFAULT_OUT_DIR}"
mkdir -p "$OUT_DIR"
API_URL="${OMEGA_SMOKE_API_URL:-http://127.0.0.1:8001}"
WAIT_SECONDS="${OMEGA_RESTART_WAIT_SECONDS:-60}"
FORCE_START="${OMEGA_RESTART_FORCE_START:-0}"
SKIP_PREFLIGHT="${OMEGA_RESTART_SKIP_PREFLIGHT:-1}"
SUMMARY_FILE="$OUT_DIR/gate_summary.txt"

SMOKE_CMD="$ROOT_DIR/scripts/stabilization/smoke_gate.sh"
if [ ! -x "$SMOKE_CMD" ]; then
  echo "Missing executable smoke gate script: $SMOKE_CMD"
  exit 1
fi

echo "Restart gate starting..."
echo "loops=$LOOPS"
echo "out_dir=$OUT_DIR"

pass_count=0
status="running"
failed_iteration=""
failed_stage=""
failed_log=""

write_summary() {
  {
    echo "generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "status=$status"
    echo "loops=$LOOPS"
    echo "passed_loops=$pass_count"
    echo "api_url=$API_URL"
    echo "wait_seconds=$WAIT_SECONDS"
    echo "force_start=$FORCE_START"
    echo "skip_preflight=$SKIP_PREFLIGHT"
    echo "out_dir=$OUT_DIR"
    echo "failed_iteration=$failed_iteration"
    echo "failed_stage=$failed_stage"
    echo "failed_log=$failed_log"
  } >"$SUMMARY_FILE"
}

trap write_summary EXIT

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
    if [ "$elapsed" -ge "$WAIT_SECONDS" ]; then
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
}

for i in $(seq 1 "$LOOPS"); do
  ITER_DIR="$OUT_DIR/iter-$i"
  mkdir -p "$ITER_DIR"
  echo "=== iteration $i/$LOOPS ==="

  {
    echo "[iteration $i] start_omega"
    OMEGA_FORCE_START="$FORCE_START" OMEGA_SKIP_PREFLIGHT="$SKIP_PREFLIGHT" OMEGA_NO_TAIL=1 "$ROOT_DIR/start_omega.sh"
  } >"$ITER_DIR/start.log" 2>&1 || {
    status="failed"
    failed_iteration="$i"
    failed_stage="start_omega"
    failed_log="$ITER_DIR/start.log"
    echo "Iteration $i failed at startup. See: $ITER_DIR/start.log"
    exit 1
  }

  if ! wait_for_api_readiness; then
    {
      echo "[iteration $i] readiness_wait_failed"
      echo "api_url=$API_URL"
      echo "wait_seconds=$WAIT_SECONDS"
    } >"$ITER_DIR/readiness.log"
    status="failed"
    failed_iteration="$i"
    failed_stage="readiness_wait"
    failed_log="$ITER_DIR/readiness.log"
    echo "Iteration $i failed readiness wait. See: $ITER_DIR/readiness.log"
    exit 1
  fi

  {
    echo "[iteration $i] smoke_gate"
    "$SMOKE_CMD"
  } >"$ITER_DIR/smoke.log" 2>&1 || {
    status="failed"
    failed_iteration="$i"
    failed_stage="smoke_gate"
    failed_log="$ITER_DIR/smoke.log"
    echo "Iteration $i failed smoke gate. See: $ITER_DIR/smoke.log"
    exit 1
  }

  {
    echo "[iteration $i] baseline_capture"
    "$ROOT_DIR/scripts/stabilization/capture_baseline.sh"
  } >"$ITER_DIR/baseline.log" 2>&1 || {
    status="failed"
    failed_iteration="$i"
    failed_stage="baseline_capture"
    failed_log="$ITER_DIR/baseline.log"
    echo "Iteration $i failed baseline capture. See: $ITER_DIR/baseline.log"
    exit 1
  }

  pass_count=$((pass_count + 1))
done

status="passed"
echo "Restart gate passed: $pass_count/$LOOPS"
echo "Artifacts: $OUT_DIR"
