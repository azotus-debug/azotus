#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API_URL="${OMEGA_SMOKE_API_URL:-http://127.0.0.1:8001}"
FRONTEND_URL="${OMEGA_SMOKE_FRONTEND_URL:-http://127.0.0.1:3000}"
MIN_PROGRAMS="${OMEGA_SMOKE_MIN_PROGRAMS:-1}"
LIMIT="${OMEGA_LIBRARY_LIMIT:-5000}"
RETRIES="${OMEGA_LIBRARY_RETRIES:-3}"
RETRY_DELAY_SECONDS="${OMEGA_LIBRARY_RETRY_DELAY_SECONDS:-2}"
REPORT_FILE="${OMEGA_LIBRARY_REPORT_FILE:-}"

if [ -f "$ROOT_DIR/.omega_secrets" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.omega_secrets"
fi
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail_count=0
api_url="$API_URL/api/v2/programs?limit=$LIMIT"
frontend_url="$FRONTEND_URL/api/v2/programs?limit=$LIMIT"

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
  code="$(curl -sS -m 15 -o "$out" -w "%{http_code}" "$url" 2>/dev/null || true)"
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
  while [ "$attempt" -le "$RETRIES" ]; do
    code="$(fetch_status "$url" "$out")"
    if [ "$code" = "200" ]; then
      pass "$label -> 200"
      return 0
    fi
    if [ "$attempt" -lt "$RETRIES" ]; then
      sleep "$RETRY_DELAY_SECONDS"
    fi
    attempt=$((attempt + 1))
  done
  fail "$label -> expected 200, got $code after $RETRIES attempts ($url)"
  return 1
}

extract_program_ids() {
  local label="$1"
  local in_path="$2"
  local out_path="$3"
  python3 - <<'PY' "$label" "$in_path" "$out_path"
import json
import sys

label, in_path, out_path = sys.argv[1:]
try:
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("payload is not a list")
except Exception as exc:
    print(f"{label}_parse_error={exc}", file=sys.stderr)
    sys.exit(2)

ids = sorted({str(item.get("id")).strip() for item in data if isinstance(item, dict) and item.get("id")})
with open(out_path, "w", encoding="utf-8") as out_f:
    for pid in ids:
        out_f.write(f"{pid}\n")
print(f"{label}_count={len(ids)}")
PY
}

extract_db_ids() {
  local out_path="$1"
  local limit="$2"
  python3 - <<'PY' "$out_path" "$limit"
import os
import sys
import psycopg2

out_path = sys.argv[1]
limit = int(sys.argv[2])

dsn = (os.getenv("DB_DSN") or os.getenv("DATABASE_URL") or "").strip()
if dsn:
    conn = psycopg2.connect(dsn, connect_timeout=8)
else:
    user = os.getenv("DB_USER") or os.getenv("OMEGA_PG_USER") or "postgres"
    password = os.getenv("DB_PASS") or os.getenv("OMEGA_PG_PASS") or ""
    dbname = os.getenv("DB_NAME") or os.getenv("OMEGA_PG_DB") or "postgres"
    host = os.getenv("DB_HOST") or os.getenv("OMEGA_PG_HOST") or "127.0.0.1"
    port = os.getenv("DB_PORT") or os.getenv("OMEGA_PG_PORT") or "5432"
    conn = psycopg2.connect(
        user=user,
        password=password,
        dbname=dbname,
        host=host,
        port=port,
        connect_timeout=8,
    )

with conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM programs
            WHERE COALESCE(status, 'ACTIVE') != 'DELETED'
            ORDER BY updated_at DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        ids = sorted({str(row[0]).strip() for row in cur.fetchall() if row and row[0]})

with open(out_path, "w", encoding="utf-8") as out_f:
    for pid in ids:
        out_f.write(f"{pid}\n")
print(f"db_count={len(ids)}")
PY
}

count_ids() {
  local path="$1"
  if [ -f "$path" ]; then
    awk 'END{print NR+0}' "$path"
  else
    echo "0"
  fi
}

echo "Running library integrity gate..."
echo "API_URL=$API_URL"
echo "FRONTEND_URL=$FRONTEND_URL"
echo "LIMIT=$LIMIT"

assert_http_200 "backend programs endpoint" "$api_url" "$TMP_DIR/api_programs.json"
assert_http_200 "frontend programs proxy endpoint" "$frontend_url" "$TMP_DIR/frontend_programs.json"

if ! extract_program_ids "api" "$TMP_DIR/api_programs.json" "$TMP_DIR/api_ids.txt" >/dev/null 2>"$TMP_DIR/api_parse.err"; then
  fail "failed parsing backend programs JSON: $(cat "$TMP_DIR/api_parse.err" 2>/dev/null || echo unknown)"
fi

if ! extract_program_ids "frontend" "$TMP_DIR/frontend_programs.json" "$TMP_DIR/frontend_ids.txt" >/dev/null 2>"$TMP_DIR/frontend_parse.err"; then
  fail "failed parsing frontend programs JSON: $(cat "$TMP_DIR/frontend_parse.err" 2>/dev/null || echo unknown)"
fi

if ! extract_db_ids "$TMP_DIR/db_ids.txt" "$LIMIT" >/dev/null 2>"$TMP_DIR/db.err"; then
  fail "failed querying DB programs: $(cat "$TMP_DIR/db.err" 2>/dev/null || echo unknown)"
fi

db_count="$(count_ids "$TMP_DIR/db_ids.txt")"
api_count="$(count_ids "$TMP_DIR/api_ids.txt")"
frontend_count="$(count_ids "$TMP_DIR/frontend_ids.txt")"

if [ "$db_count" -ge "$MIN_PROGRAMS" ]; then
  pass "db program count >= $MIN_PROGRAMS (actual=$db_count)"
else
  fail "db program count < $MIN_PROGRAMS (actual=$db_count)"
fi

if [ "$api_count" -ge "$MIN_PROGRAMS" ]; then
  pass "api program count >= $MIN_PROGRAMS (actual=$api_count)"
else
  fail "api program count < $MIN_PROGRAMS (actual=$api_count)"
fi

if [ "$frontend_count" -ge "$MIN_PROGRAMS" ]; then
  pass "frontend program count >= $MIN_PROGRAMS (actual=$frontend_count)"
else
  fail "frontend program count < $MIN_PROGRAMS (actual=$frontend_count)"
fi

if [ "$db_count" -eq "$api_count" ]; then
  pass "db/api count parity (actual=$db_count)"
else
  fail "db/api count mismatch (db=$db_count api=$api_count)"
fi

if [ "$db_count" -eq "$frontend_count" ]; then
  pass "db/frontend count parity (actual=$db_count)"
else
  fail "db/frontend count mismatch (db=$db_count frontend=$frontend_count)"
fi

missing_in_api_count="$(comm -23 "$TMP_DIR/db_ids.txt" "$TMP_DIR/api_ids.txt" | awk 'END{print NR+0}')"
extra_in_api_count="$(comm -13 "$TMP_DIR/db_ids.txt" "$TMP_DIR/api_ids.txt" | awk 'END{print NR+0}')"
missing_in_frontend_count="$(comm -23 "$TMP_DIR/db_ids.txt" "$TMP_DIR/frontend_ids.txt" | awk 'END{print NR+0}')"
extra_in_frontend_count="$(comm -13 "$TMP_DIR/db_ids.txt" "$TMP_DIR/frontend_ids.txt" | awk 'END{print NR+0}')"

if [ "$missing_in_api_count" -eq 0 ] && [ "$extra_in_api_count" -eq 0 ]; then
  pass "db/api ID parity"
else
  fail "db/api ID mismatch (missing_in_api=$missing_in_api_count extra_in_api=$extra_in_api_count)"
  echo "api_missing_sample=$(comm -23 "$TMP_DIR/db_ids.txt" "$TMP_DIR/api_ids.txt" | sed -n '1,10p' | tr '\n' ',' | sed 's/,$//')"
  echo "api_extra_sample=$(comm -13 "$TMP_DIR/db_ids.txt" "$TMP_DIR/api_ids.txt" | sed -n '1,10p' | tr '\n' ',' | sed 's/,$//')"
fi

if [ "$missing_in_frontend_count" -eq 0 ] && [ "$extra_in_frontend_count" -eq 0 ]; then
  pass "db/frontend ID parity"
else
  fail "db/frontend ID mismatch (missing_in_frontend=$missing_in_frontend_count extra_in_frontend=$extra_in_frontend_count)"
  echo "frontend_missing_sample=$(comm -23 "$TMP_DIR/db_ids.txt" "$TMP_DIR/frontend_ids.txt" | sed -n '1,10p' | tr '\n' ',' | sed 's/,$//')"
  echo "frontend_extra_sample=$(comm -13 "$TMP_DIR/db_ids.txt" "$TMP_DIR/frontend_ids.txt" | sed -n '1,10p' | tr '\n' ',' | sed 's/,$//')"
fi

if [ -n "$REPORT_FILE" ]; then
  {
    echo "generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "api_url=$api_url"
    echo "frontend_url=$frontend_url"
    echo "limit=$LIMIT"
    echo "db_count=$db_count"
    echo "api_count=$api_count"
    echo "frontend_count=$frontend_count"
    echo "missing_in_api_count=$missing_in_api_count"
    echo "extra_in_api_count=$extra_in_api_count"
    echo "missing_in_frontend_count=$missing_in_frontend_count"
    echo "extra_in_frontend_count=$extra_in_frontend_count"
    echo "fail_count=$fail_count"
  } >"$REPORT_FILE"
fi

if [ "$fail_count" -eq 0 ]; then
  echo "Library integrity gate PASSED."
  exit 0
fi

echo "Library integrity gate FAILED ($fail_count checks failed)."
exit 1
