#!/bin/bash
# =============================================================================
# Uninstall Omega Burn Agent LaunchAgent
# =============================================================================

set -euo pipefail

PLIST_DEST="$HOME/Library/LaunchAgents/com.omega.burn-agent.plist"

echo "🔧 Uninstalling Omega Burn Agent..."

if [ -f "$PLIST_DEST" ]; then
    # Unload the agent
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    
    # Remove plist
    rm "$PLIST_DEST"
    
    echo "✅ Burn Agent uninstalled successfully!"
else
    echo "ℹ️  Burn Agent was not installed."
fi
