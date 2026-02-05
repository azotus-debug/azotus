#!/bin/bash
# =============================================================================
# Install Omega Burn Agent as a LaunchAgent
# =============================================================================
# 
# This script installs the burn agent to run automatically at login.
# 
# Usage:
#   bash scripts/install_burn_agent.sh
#
# Prerequisites:
#   1. Edit scripts/com.omega.burn-agent.plist with your settings:
#      - OMEGA_CLOUD_MANAGER_URL
#      - OMEGA_CLOUD_MANAGER_TOKEN
#      - OMEGA_BURN_AGENT_ID
#      - Output directories
#   
#   2. Ensure the Cloud Manager is deployed and accessible
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SOURCE="$SCRIPT_DIR/com.omega.burn-agent.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.omega.burn-agent.plist"

echo "🔧 Installing Omega Burn Agent..."

# Check if plist exists
if [ ! -f "$PLIST_SOURCE" ]; then
    echo "❌ Error: $PLIST_SOURCE not found"
    exit 1
fi

# Check if already installed
if [ -f "$PLIST_DEST" ]; then
    echo "⚠️  Burn agent already installed. Unloading existing..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Create LaunchAgents directory if needed
mkdir -p "$HOME/Library/LaunchAgents"

# Copy plist
cp "$PLIST_SOURCE" "$PLIST_DEST"

# Validate plist
if ! plutil -lint "$PLIST_DEST" > /dev/null 2>&1; then
    echo "❌ Error: Invalid plist format"
    exit 1
fi

# Load the agent
launchctl load "$PLIST_DEST"

echo ""
echo "✅ Burn Agent installed successfully!"
echo ""
echo "📍 Status:"
launchctl list | grep omega || echo "   (not yet started)"
echo ""
echo "📋 Logs:"
echo "   tail -f /tmp/omega-burn-agent.log"
echo "   tail -f /tmp/omega-burn-agent.err"
echo ""
echo "🔧 To uninstall:"
echo "   bash scripts/uninstall_burn_agent.sh"
echo ""
