#!/bin/bash
# Omega Pro Smoke Test - Quick Health Check
# Runs in <30 seconds. Use before any testing session.

set -e

echo "=== Omega Pro Smoke Test ==="
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✅ $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; exit 1; }

# 1. Backend alive?
echo "Checking backend..."
curl -sf http://localhost:8080/api/v2/programs > /dev/null || fail "Backend not responding (port 8080)"
pass "Backend responding"

# 2. Frontend alive?
echo "Checking frontend..."
curl -sf http://localhost:3000 > /dev/null || fail "Frontend not responding (port 3000)"
pass "Frontend responding"

# 3. Database has programs?
echo "Checking database..."
COUNT=$(curl -s http://localhost:8080/api/v2/programs | python3 -c "import sys,json;print(len(json.load(sys.stdin)))")
[ "$COUNT" -gt 0 ] || fail "No programs in database"
pass "Database has $COUNT programs"

# 4. Pipeline stats work?
echo "Checking pipeline stats..."
STATS=$(curl -s http://localhost:8080/api/v2/pipeline/stats)
echo "$STATS" | python3 -c "import sys,json;d=json.load(sys.stdin);assert 'total_active' in d" || fail "Pipeline stats malformed"
pass "Pipeline stats OK"

# 5. Languages endpoint?
echo "Checking languages..."
LANG_COUNT=$(curl -s http://localhost:8080/api/v2/languages | python3 -c "import sys,json;print(len(json.load(sys.stdin)['languages']))")
[ "$LANG_COUNT" -gt 0 ] || fail "Languages endpoint returned empty"
pass "Languages endpoint OK ($LANG_COUNT languages)"

# 6. Voices endpoint?
echo "Checking voices..."
VOICE_COUNT=$(curl -s http://localhost:8080/api/v2/voices | python3 -c "import sys,json;print(len(json.load(sys.stdin)['voices']))")
[ "$VOICE_COUNT" -gt 0 ] || fail "Voices endpoint returned empty"
pass "Voices endpoint OK ($VOICE_COUNT voices)"

# 7. Active tracks?
echo "Checking active tracks..."
ACTIVE=$(curl -s http://localhost:8080/api/v2/tracks/active | python3 -c "import sys,json;print(len(json.load(sys.stdin)))")
pass "Active tracks: $ACTIVE"

echo ""
echo "========================================"
echo -e "${GREEN}🎉 All smoke tests passed!${NC}"
echo "========================================"
