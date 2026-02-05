#!/bin/bash

# Omega Pro - Seamless Start Script
# Usage: ./start_all.sh

echo "🚀 Omega Pro is starting..."

# 1. Get script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# 2. Kill legacy processes (clean slate)
echo "🧹 Cleaning up old processes..."
pkill -f "omega_manager.py"
pkill -f "dashboard.py"
pkill -f "next-server" || true

# 3. Setup Virtual Environment
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment (first run only)..."
    /opt/homebrew/bin/python3.11 -m venv .venv
fi

# Ensure dependencies are installed (fast check)
if [ ! -f ".venv/bin/ina_speech_segmenter" ]; then
    echo "📥 Installing dependencies..."
    .venv/bin/pip install -r requirements.txt
fi

# 4. Activate Venc
source .venv/bin/activate

# 5. Start Backend Services
echo "🤖 Starting Omega Manager (Scheduler)..."
python omega_manager.py > logs/manager.log 2>&1 &
MANAGER_PID=$!

echo "📊 Starting Omega Dashboard (API)..."
python dashboard.py > logs/dashboard.log 2>&1 &
DASHBOARD_PID=$!

# Wait for backend to warm up
sleep 3

# 6. Start Frontend
echo "💻 Starting Frontend..."
cd omega-frontend

# Trap Ctrl+C to kill everything
trap "echo '🛑 Stopping all services...'; kill $MANAGER_PID $DASHBOARD_PID; exit" INT TERM

npm run dev
