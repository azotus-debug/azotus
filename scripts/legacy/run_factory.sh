#!/bin/bash

# 1. Activate Environment
source venv/bin/activate

# 2. Create Logs Directory (Crucial step you were missing)
mkdir -p logs

# 3. Trap Ctrl+C to kill all background processes
# 3. Trap Ctrl+C to kill all background processes
trap 'pkill -P $$' SIGINT

# 3b. CLEANUP: Kill any existing instances to prevent loops
echo "🧹 Cleaning up old processes..."
pkill -f "auto_skeleton.py" || true
pkill -f "cloud_brain.py" || true
pkill -f "finalize.py" || true
pkill -f "publisher.py" || true
pkill -f "editor.py" || true
pkill -f "archivist.py"
pkill -f "process_watchdog.py" || true
rm /tmp/*.lock 2>/dev/null || true
sleep 1

echo "---------------------------------------------------"
echo "🚀  SERMON FACTORY IS LIVE"
echo "    (Logs are being saved to the 'logs/' folder)"
echo "---------------------------------------------------"

# 4. Start Robots with Logging enabled
# 'python3 -u' means 'Unbuffered' so you see text instantly

echo "👂 Starting Ear..."
python3 -u auto_skeleton.py > logs/ear.log 2>&1 &

echo "🧠 Starting Brain..."
python3 -u cloud_brain.py > logs/brain.log 2>&1 &



echo "   Starting The Hand (Typesetter)..."
nohup python3 -u finalize.py > logs/hand.log 2>&1 &

echo "   Starting The Publisher (Burn-in)..."
nohup python3 -u publisher.py > logs/publisher.log 2>&1 &

echo "   Starting The Archivist (Cleanup)..."
nohup ./venv/bin/python3 -u archivist.py > logs/archivist.log 2>&1 &
nohup ./venv/bin/python3 -u process_watchdog.py > logs/watchdog.log 2>&1 &

# 5. Launch the Live Dashboard immediately (with Sleep Prevention)
echo "✅ Factory Started. Launching Dashboard..."
echo "☕️  Keeping Mac awake while running..."
# Use caffeinate to keep system awake while streamlit runs
caffeinate -i ./venv/bin/streamlit run dashboard.py --server.runOnSave true --server.headless true
