#!/bin/bash
# Log Rotation Script for OmegaTV
# Prevents logs from filling up disk space
# Run via cron: */30 * * * * /path/to/rotate_logs.sh

LOG_DIR="$(dirname "$0")/logs"
MAX_SIZE_MB=100  # Rotate when log exceeds 100MB
KEEP_ROTATIONS=3

rotate_log() {
    local log_file="$1"
    local base_name=$(basename "$log_file")
    
    if [ ! -f "$log_file" ]; then
        return
    fi
    
    # Get file size in MB
    local size_mb=$(du -m "$log_file" 2>/dev/null | cut -f1)
    
    if [ "${size_mb:-0}" -gt "$MAX_SIZE_MB" ]; then
        echo "$(date): Rotating $base_name (${size_mb}MB > ${MAX_SIZE_MB}MB)"
        
        # Remove oldest rotation
        rm -f "${log_file}.${KEEP_ROTATIONS}" 2>/dev/null
        
        # Shift existing rotations
        for i in $(seq $((KEEP_ROTATIONS-1)) -1 1); do
            if [ -f "${log_file}.${i}" ]; then
                mv "${log_file}.${i}" "${log_file}.$((i+1))"
            fi
        done
        
        # Rotate current log
        mv "$log_file" "${log_file}.1"
        
        # Create fresh log
        touch "$log_file"
        
        echo "$(date): Rotated $base_name successfully"
    fi
}

# Rotate main logs
rotate_log "$LOG_DIR/manager.log"
rotate_log "$LOG_DIR/cloud_sync.log"
rotate_log "$LOG_DIR/dashboard.log"
rotate_log "$LOG_DIR/watchdog.log"

echo "$(date): Log rotation check complete"
