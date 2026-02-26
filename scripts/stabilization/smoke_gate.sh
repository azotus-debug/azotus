#!/usr/bin/env bash
set -euo pipefail

API_URL="${OMEGA_SMOKE_API_URL:-http://127.0.0.1:8001}"
FRONTEND_URL="${OMEGA_SMOKE_FRONTEND_URL:-http://127.0.0.1:3000}"
MIN_PROGRAMS="${OMEGA_SMOKE_MIN_PROGRAMS:-1}"
SMOKE_RETRIES="${OMEGA_SMOKE_RETRIES:-3}"
SMOKE_RETRY_DELAY_SECONDS="${OMEGA_SMOKE_RETRY_DELAY_SECONDS:-2}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail_count=0

pass() {
  echo "[PASS] $1"
}

fail() {
  echo "[FAIL] $1"
  fail_count=$((fail_count + 1))
}

fetch_status() {
  local url="$1"
  local out="$2"
  local code
  code="$(curl -sS -m 10 -o "$out" -w "%{http_code}" "$url" 2>/dev/null || true)"
  if [[ "$code" =~ ^[0-9]{3}$ ]]; then
    echo "$code"
  else
    echo "000"
  fi
}

assert_http_200() {
  local label="$1"
  local url="$2"
  local out="$3"
  local code="000"
  local attempt=1
  while [ "$attempt" -le "$SMOKE_RETRIES" ]; do
    code="$(fetch_status "$url" "$out")"
    if [ "$code" = "200" ]; then
      pass "$label -> 200"
      return 0
    fi
    if [ "$attempt" -lt "$SMOKE_RETRIES" ]; then
      sleep "$SMOKE_RETRY_DELAY_SECONDS"
    fi
    attempt=$((attempt + 1))
  done
  fail "$label -> expected 200, got $code after $SMOKE_RETRIES attempts ($url)"
  return 1
}

echo "Running smoke gate..."
echo "API_URL=$API_URL"
echo "FRONTEND_URL=$FRONTEND_URL"

assert_http_200 "api health" "$API_URL/api/health" "$TMP_DIR/api_health.json"
assert_http_200 "api stuck health" "$API_URL/api/v2/health/stuck" "$TMP_DIR/api_stuck.json"
assert_http_200 "api programs" "$API_URL/api/v2/programs" "$TMP_DIR/api_programs.json"
assert_http_200 "frontend root" "$FRONTEND_URL/" "$TMP_DIR/frontend_root.html"
assert_http_200 "frontend programs proxy" "$FRONTEND_URL/api/v2/programs" "$TMP_DIR/frontend_programs.json"

css_path=""
js_path=""
if [ -f "$TMP_DIR/frontend_root.html" ]; then
  css_path="$(grep -oE '/_next/static[^"]+\.css' "$TMP_DIR/frontend_root.html" | head -n 1 || true)"
  js_path="$(grep -oE '/_next/static[^"]+\.js' "$TMP_DIR/frontend_root.html" | head -n 1 || true)"
fi

if [ -n "$css_path" ]; then
  assert_http_200 "frontend css asset" "$FRONTEND_URL$css_path" "$TMP_DIR/asset.css"
else
  fail "frontend css asset path missing from root HTML"
fi

if [ -n "$js_path" ]; then
  assert_http_200 "frontend js asset" "$FRONTEND_URL$js_path" "$TMP_DIR/asset.js"
else
  fail "frontend js asset path missing from root HTML"
fi

json_list_count() {
  local json_path="$1"
  python3 - <<'PY' "$json_path"
import json,sys
path=sys.argv[1]
try:
    data=json.load(open(path))
    if isinstance(data,list):
        print(len(data))
    else:
        print(0)
except Exception:
    print(0)
PY
}

api_program_count="$(json_list_count "$TMP_DIR/api_programs.json")"
frontend_program_count="$(json_list_count "$TMP_DIR/frontend_programs.json")"

if [ "$api_program_count" -ge "$MIN_PROGRAMS" ]; then
  pass "api program count >= $MIN_PROGRAMS (actual=$api_program_count)"
else
  fail "api program count < $MIN_PROGRAMS (actual=$api_program_count)"
fi

if [ "$frontend_program_count" -ge "$MIN_PROGRAMS" ]; then
  pass "frontend program count >= $MIN_PROGRAMS (actual=$frontend_program_count)"
else
  fail "frontend program count < $MIN_PROGRAMS (actual=$frontend_program_count)"
fi

if [ "$api_program_count" -eq "$frontend_program_count" ]; then
  pass "program count parity api/frontend (actual=$api_program_count)"
else
  fail "program count parity mismatch (api=$api_program_count frontend=$frontend_program_count)"
fi

if [ "$fail_count" -eq 0 ]; then
  echo "Smoke gate PASSED."
  exit 0
fi

echo "Smoke gate FAILED ($fail_count checks failed)."
exit 1
