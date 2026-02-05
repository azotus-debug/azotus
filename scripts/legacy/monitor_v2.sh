#!/bin/bash
clear
echo "=================================================="
echo "   Ω  OMEGA MANAGER STATUS  Ω"
echo "=================================================="
echo ""

# Check Manager
if pgrep -f "omega_manager.py" > /dev/null; then
    echo -e " 🧠 Manager:      \033[32mRUNNING\033[0m"
else
    echo -e " 🧠 Manager:      \033[31mSTOPPED\033[0m"
fi

echo ""
echo "=================================================="
echo "   📝 RECENT LOGS"
echo "=================================================="
tail -n 15 logs/manager.log 2>/dev/null
echo ""
echo "=================================================="
