#!/bin/bash
# =============================================================================
# OMEGA PREFLIGHT CHECK
# Run before starting a batch to verify system readiness
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
WARN="${YELLOW}⚠${NC}"

echo "============================================"
echo "  OMEGA PREFLIGHT CHECK"
echo "============================================"
echo ""

ERRORS=0
WARNINGS=0

# 1. Check if storage is mounted (base dir or external override)
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STORAGE_ROOT="${OMEGA_STORAGE_ROOT:-$BASE_DIR}"
echo -n "1. Storage mounted... "
if [ -d "$STORAGE_ROOT/1_INBOX" ]; then
    echo -e "$PASS"
else
    echo -e "$FAIL Not mounted"
    ((ERRORS++))
fi

# 2. Check disk space (need at least 50GB)
echo -n "2. Disk space (need 50GB+)... "
DELIVERY_PATH="${DELIVERY_PATH:-$BASE_DIR/4_DELIVERY}"
if [ -d "$DELIVERY_PATH" ]; then
    AVAIL=$(df -g "$DELIVERY_PATH" | tail -1 | awk '{print $4}')
    if [ "$AVAIL" -ge 50 ]; then
        echo -e "$PASS ${AVAIL}GB available"
    elif [ "$AVAIL" -ge 20 ]; then
        echo -e "$WARN ${AVAIL}GB available (low)"
        ((WARNINGS++))
    else
        echo -e "$FAIL ${AVAIL}GB available (critical)"
        ((ERRORS++))
    fi
else
    echo -e "$WARN Cannot check"
    ((WARNINGS++))
fi

# 3. Check omega_manager running
echo -n "3. Omega Manager running... "
if pgrep -f "omega_manager.py" > /dev/null; then
    echo -e "$PASS"
else
    echo -e "$FAIL Not running"
    ((ERRORS++))
fi

# 4. Check dashboard running
echo -n "4. Dashboard running... "
if curl -s "http://localhost:8080/health" > /dev/null 2>&1; then
    echo -e "$PASS"
else
    echo -e "$WARN Not accessible (optional)"
    ((WARNINGS++))
fi

# 5. Check Google Cloud credentials
echo -n "5. Google Cloud credentials... "
if gcloud auth application-default print-access-token > /dev/null 2>&1; then
    echo -e "$PASS"
else
    echo -e "$FAIL Not authenticated"
    ((ERRORS++))
fi

# 5b. Check Gemini 3 Connectivity (Soft Check)
echo -n "5b. Gemini 3 API Access... "
if curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp?key=${GOOGLE_API_KEY}" > /dev/null; then
    echo -e "$PASS (API Reachable)"
else
    # We don't error-out because Vertex AI doesn't always respond to simple curls
    echo -e "$WARN (Verify manually if using Vertex)"
fi

# 6. Check INBOX directories writable
echo -n "6. INBOX writable... "
INBOX_PATH="${INBOX_PATH:-$(pwd)/1_INBOX}"
if [ -w "$INBOX_PATH/01_AUTO_PILOT/Classic" ] 2>/dev/null; then
    echo -e "$PASS"
else
    echo -e "$FAIL Not writable"
    ((ERRORS++))
fi

# 7. Count pending jobs in INBOX
echo -n "7. Pending files in INBOX... "
PENDING=$(find "$INBOX_PATH" -type f \( -name "*.mp4" -o -name "*.mov" -o -name "*.mkv" \) 2>/dev/null | wc -l | tr -d ' ')
echo -e "${GREEN}${PENDING}${NC} files"

# 8. Check current job queue
echo -n "8. Active jobs in database... "
if [ -f "$(pwd)/omega_jobs.db" ]; then
    ACTIVE=$(sqlite3 "$(pwd)/omega_jobs.db" "SELECT COUNT(*) FROM tracks WHERE stage NOT IN ('COMPLETED', 'FAILED', 'ARCHIVED');" 2>/dev/null || echo "?")
    echo -e "${YELLOW}${ACTIVE}${NC} active"
else
    echo -e "$WARN No database found"
    ((WARNINGS++))
fi

# Summary
echo ""
echo "============================================"
if [ $ERRORS -eq 0 ]; then
    if [ $WARNINGS -eq 0 ]; then
        echo -e "  ${GREEN}ALL CHECKS PASSED${NC}"
    else
        echo -e "  ${YELLOW}PASSED WITH $WARNINGS WARNING(S)${NC}"
    fi
    echo "  Ready for batch processing!"
    echo "============================================"
    exit 0
else
    echo -e "  ${RED}FAILED: $ERRORS ERROR(S)${NC}"
    echo "  Fix issues before starting batch."
    echo "============================================"
    exit 1
fi
