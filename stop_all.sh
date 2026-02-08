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

# Kill any orphaned Next.js frontend processes for this workspace
FRONTEND_PATH_PATTERN="/Users/haukurhauksson/Azotus/omega-frontend/node_modules/.bin/next"
pids=$(ps aux | grep "$FRONTEND_PATH_PATTERN" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing orphaned Next.js PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

# Kill orphaned next-server workers for this workspace only
NEXT_SERVER_PIDS=$(ps aux | awk '/[n]ext-server/{print $2}')
LOCAL_NEXT_SERVER_PIDS=""
for pid in $NEXT_SERVER_PIDS; do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)
    case "$cwd" in
        */Azotus/omega-frontend*)
            LOCAL_NEXT_SERVER_PIDS="$LOCAL_NEXT_SERVER_PIDS $pid"
            ;;
    esac
done
if [ -n "$LOCAL_NEXT_SERVER_PIDS" ]; then
    echo "   Killing orphaned next-server PIDs:$LOCAL_NEXT_SERVER_PIDS"
    kill -9 $LOCAL_NEXT_SERVER_PIDS 2>/dev/null
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

# Kill Watchdog Supervisor
WATCHDOG_SUP_PID_FILE="/tmp/omega_watchdog_supervisor.pid"
if [ -f "$WATCHDOG_SUP_PID_FILE" ]; then
    pid=$(cat "$WATCHDOG_SUP_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing watchdog supervisor PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$WATCHDOG_SUP_PID_FILE"
fi

# Kill process_watchdog.py (fallback)
pids=$(ps aux | grep "[p]rocess_watchdog.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing process_watchdog.py PIDs: $pids"
    kill -9 $pids 2>/dev/null
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

# Kill watchdog_supervisor.py (fallback)
pids=$(ps aux | grep "[w]atchdog_supervisor.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing watchdog_supervisor.py PIDs: $pids"
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
