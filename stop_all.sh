#!/bin/bash

echo "🛑 Stopping all OmegaTV processes..."

# Kill Frontend (Next.js)
FRONTEND_PID_FILE="/tmp/omega_frontend.pid"
if [ -f "$FRONTEND_PID_FILE" ]; then
    pid=$(cat "$FRONTEND_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing frontend PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$FRONTEND_PID_FILE"
fi

# Kill Watchdog
WATCHDOG_PID_FILE="/tmp/omega_watchdog.pid"
if [ -f "$WATCHDOG_PID_FILE" ]; then
    pid=$(cat "$WATCHDOG_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing watchdog PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$WATCHDOG_PID_FILE"
fi

# Kill Cloud Sync daemon
CLOUD_SYNC_PID_FILE="/tmp/omega_cloud_sync.pid"
if [ -f "$CLOUD_SYNC_PID_FILE" ]; then
    pid=$(cat "$CLOUD_SYNC_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing cloud_sync_service PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$CLOUD_SYNC_PID_FILE"
fi

# Kill cloud_sync_service.py (fallback)
pids=$(ps aux | grep "[c]loud_sync_service.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing cloud_sync_service.py PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

# Kill Cloud SQL Proxy
CLOUD_SQL_PROXY_PID_FILE="/tmp/omega_cloud_sql_proxy.pid"
if [ -f "$CLOUD_SQL_PROXY_PID_FILE" ]; then
    pid=$(cat "$CLOUD_SQL_PROXY_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing Cloud SQL Proxy PID: $pid"
        kill "$pid" 2>/dev/null
    fi
    rm -f "$CLOUD_SQL_PROXY_PID_FILE"
fi

# Kill Caffeinate (sleep prevention)
CAFFEINATE_PID_FILE="/tmp/omega_caffeinate.pid"
if [ -f "$CAFFEINATE_PID_FILE" ]; then
    pid=$(cat "$CAFFEINATE_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing caffeinate PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$CAFFEINATE_PID_FILE"
fi

# Kill Dashboard (and all Python processes running dashboard.py)
pids=$(ps aux | grep "[d]ashboard.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing dashboard.py PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

# Kill Omega Manager
pids=$(ps aux | grep "[o]mega_manager.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing omega_manager.py PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

# Kill FFmpeg (be careful not to kill system ffmpeg if used elsewhere, but for this user it's likely safe)
pids=$(ps aux | grep "[f]fmpeg" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing ffmpeg PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

echo "✅ All processes stopped."
