#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_BASE="$ROOT_DIR/docs/stabilization/artifacts"
TS="$(date +%Y%m%dT%H%M%S%z)"
OUT_DIR="${OMEGA_SOAK_OUT_DIR:-$ARTIFACT_BASE/soak-gate-$TS}"
LOG_DIR="$OUT_DIR/logs"
RELEASE_GATE_SCRIPT="$ROOT_DIR/scripts/stabilization/release_gate.sh"

SOAK_HOURS="${OMEGA_SOAK_HOURS:-24}"
INTERVAL_SECONDS="${OMEGA_SOAK_INTERVAL_SECONDS:-3600}"
RELEASE_RESTART_LOOPS="${OMEGA_SOAK_RELEASE_RESTART_LOOPS:-1}"
SUMMARY_FILE="$OUT_DIR/soak_summary.txt"

mkdir -p "$LOG_DIR"

status="running"
current_iteration=0
passed_iterations=0
failed_iteration=""
failed_log=""
last_release_artifact=""

write_summary() {
  {
    echo "generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "status=$status"
    echo "soak_hours=$SOAK_HOURS"
    echo "interval_seconds=$INTERVAL_SECONDS"
    echo "release_restart_loops=$RELEASE_RESTART_LOOPS"
    echo "out_dir=$OUT_DIR"
    echo "current_iteration=$current_iteration"
    echo "passed_iterations=$passed_iterations"
    echo "failed_iteration=$failed_iteration"
    echo "failed_log=$failed_log"
    echo "last_release_artifact=$last_release_artifact"
    echo "pid=$$"
  } >"$SUMMARY_FILE"
}

trap write_summary EXIT

if [ ! -x "$RELEASE_GATE_SCRIPT" ]; then
  echo "Missing executable release gate script: $RELEASE_GATE_SCRIPT"
  exit 1
fi

if ! [[ "$SOAK_HOURS" =~ ^[0-9]+$ ]] || [ "$SOAK_HOURS" -lt 1 ]; then
  echo "Invalid OMEGA_SOAK_HOURS value: $SOAK_HOURS"
  exit 1
fi

if ! [[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || [ "$INTERVAL_SECONDS" -lt 1 ]; then
  echo "Invalid OMEGA_SOAK_INTERVAL_SECONDS value: $INTERVAL_SECONDS"
  exit 1
fi

if ! [[ "$RELEASE_RESTART_LOOPS" =~ ^[0-9]+$ ]] || [ "$RELEASE_RESTART_LOOPS" -lt 1 ]; then
  echo "Invalid OMEGA_SOAK_RELEASE_RESTART_LOOPS value: $RELEASE_RESTART_LOOPS"
  exit 1
fi

echo "Soak gate starting..."
echo "out_dir=$OUT_DIR"
echo "soak_hours=$SOAK_HOURS"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "release_restart_loops=$RELEASE_RESTART_LOOPS"

for i in $(seq 1 "$SOAK_HOURS"); do
  current_iteration="$i"
  ITER_LOG="$LOG_DIR/hour-$i.log"
  echo "=== soak iteration $i/$SOAK_HOURS ===" | tee -a "$ITER_LOG"

  {
    echo "[iteration $i] start=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    OMEGA_RELEASE_GATE_RESTART_LOOPS="$RELEASE_RESTART_LOOPS" "$RELEASE_GATE_SCRIPT"
    echo "[iteration $i] end=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  } >>"$ITER_LOG" 2>&1 || {
    status="failed"
    failed_iteration="$i"
    failed_log="$ITER_LOG"
    echo "Soak gate failed at iteration $i. See: $ITER_LOG"
    exit 1
  }

  # Track the nested release-gate artifact directory for quick lookup.
  artifact_line="$(grep -E "^Artifacts: " "$ITER_LOG" | tail -n 1 || true)"
  if [ -n "$artifact_line" ]; then
    last_release_artifact="${artifact_line#Artifacts: }"
  fi

  passed_iterations=$((passed_iterations + 1))
  write_summary

  if [ "$i" -lt "$SOAK_HOURS" ]; then
    sleep "$INTERVAL_SECONDS"
  fi
done

status="passed"
write_summary
echo "Soak gate PASSED: $passed_iterations/$SOAK_HOURS"
echo "Summary: $SUMMARY_FILE"
