#!/bin/bash

# Colors
GREEN='\033[1;32m'
BLUE='\033[1;34m'
CYAN='\033[1;36m'
RED='\033[1;31m'
YELLOW='\033[1;33m'
GRAY='\033[1;30m'
NC='\033[0m'

# Icons
ICON_EAR="👂"
ICON_BRAIN="🧠"
ICON_HAND="✍️ "
ICON_OK="🟢"
ICON_OFF="🔴"

check_process() {
    if pgrep -f "$1" > /dev/null; then
        echo -e "${ICON_OK} ${GREEN}ONLINE${NC}"
    else
        echo -e "${ICON_OFF} ${RED}OFFLINE${NC}"
    fi
}

list_files() {
    dir=$1
    filter=$2
    count=$(ls -1 "$dir" 2>/dev/null | grep "$filter" | wc -l)
    
    if [ "$count" -eq 0 ]; then
        echo -e "   ${GRAY}(Empty)${NC}"
    else
        # Show top 3 files
        ls -1t "$dir" 2>/dev/null | grep "$filter" | head -n 3 | while read -r file; do
            echo -e "   📄 $file"
        done
        if [ "$count" -gt 3 ]; then
            echo -e "   ${GRAY}...and $(($count - 3)) more${NC}"
        fi
    fi
}

while true; do
    clear
    echo -e "${BLUE}======================================================${NC}"
    echo -e "   🏭  ${CYAN}SERMON FACTORY: MISSION CONTROL${NC}"
    echo -e "${BLUE}======================================================${NC}"
    echo ""
    
    # --- SYSTEM HEALTH ---
    echo -e "${GRAY}SYSTEM STATUS:${NC}"
    echo -e " ${ICON_EAR}  Ear (Transcriber):   $(check_process "auto_skeleton.py")"
    echo -e " ${ICON_BRAIN}  Brain (Gemini):  $(check_process "cloud_brain.py")"
    echo -e " ${ICON_HAND}  Hand (Format):   $(check_process "finalize.py")"
    echo -e " ${ICON_HAND}  Publisher:       $(check_process "publisher.py")"
    echo ""
    echo -e "${BLUE}------------------------------------------------------${NC}"
    
    # --- QUEUES ---
    
    # INBOX
    echo -e "${BLUE}📥 INBOX (Waiting for Ear)${NC}"
    # List files that do NOT start with DONE_
    ls -1 1_INBOX 2>/dev/null | grep -v "DONE_" | grep -E ".mp3|.wav|.mp4|.m4a|.mov|.mkv" | head -n 3 | while read -r file; do
        echo -e "   💿 $file"
    done
    # Check if empty (manual check because the ls pipe is complex)
    cnt=$(ls -1 1_INBOX 2>/dev/null | grep -v "DONE_" | grep -E ".mp3|.wav|.mp4|.m4a|.mov|.mkv" | wc -l)
    if [ "$cnt" -eq 0 ]; then echo -e "   ${GRAY}(Waiting for files...)${NC}"; fi
    echo ""

    # CLOUD
    echo -e "${YELLOW}☁️  CLOUD QUEUE (Processing in Gemini)${NC}"
    
    # --- BATCH TRACKING (NEW) ---
    # Grep the last "Processing Batch" line from the log
    batch_status=$(grep "Processing Batch" logs/brain.log 2>/dev/null | tail -n 1)
    if [ ! -z "$batch_status" ]; then
        # Clean up the log line to just show the message
        clean_batch=$(echo "$batch_status" | sed 's/^[ \t]*//')
        echo -e "   ⚙️  ${CYAN}$clean_batch${NC}"
    fi
    # ----------------------------

    list_files "2_READY_FOR_CLOUD" "_SKELETON.json"
    echo ""

    # FINALIZING
    echo -e "${GREEN}✨ FINALIZING (Formatting Subtitles)${NC}"
    list_files "3_TRANSLATED_DONE" "_ICELANDIC.json"
    echo ""

    # OUTPUT
    echo -e "${BOLD}4. FINAL OUTPUT (SRT)${RESET}"
    ls -1 4_FINAL_OUTPUT | grep ".srt" | head -n 5
    echo ""

    echo -e "${BOLD}5. DELIVERABLES (Burned Video)${RESET}"
    ls -1 5_DELIVERABLES | head -n 5
    echo ""

    # ERRORS
    err_cnt=$(ls -1 99_ERRORS 2>/dev/null | wc -l)
    if [ "$err_cnt" -gt 0 ]; then
        echo -e "${RED}🚨 ERRORS DETECTED: ${err_cnt} file(s)${NC}"
        echo -e "   Run ./retry_errors.sh to fix."
    fi

    echo -e "${BLUE}======================================================${NC}"
    echo -e "   ${GRAY}Press Ctrl+C to exit${NC}"
    
    sleep 2
done
