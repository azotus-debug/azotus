#!/bin/bash

# Define directories
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"

# 1. Stop Legacy/Existing Services
echo "🛑 Stopping Legacy Services..."
pkill -f "auto_skeleton.py" || true
pkill -f "cloud_brain.py" || true
pkill -f "chief_editor.py" || true
pkill -f "finalize.py" || true
pkill -f "publisher.py" || true
pkill -f "omega_manager.py" || true

# 🛑 KILL ZOMBIES (Crucial for stability)
pkill -f "ffmpeg" || true
pkill -f "python3 -c from workers" || true

sleep 2

# 2. Start Omega Manager
echo "🚀 Starting Omega Manager..."
PYTHON_BIN="${BASE_DIR}/venv/bin/python3"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi
nohup "$PYTHON_BIN" -u omega_manager.py > logs/manager.log 2>&1 &

echo "✅ Omega Manager Active."
