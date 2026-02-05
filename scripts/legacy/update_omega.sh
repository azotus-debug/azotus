#!/bin/bash
set -e

# Omega Update Script
# Usage: ./scripts/update_omega.sh

echo "🔄 Starting Omega Update..."

# 1. Update Code
echo "📥 Pulling latest code..."
git stash
git pull
git stash pop || true

# 2. Update Dependencies
echo "🐍 Updating Python dependencies..."
pip install -r requirements.txt

echo "📦 Updating Frontend dependencies..."
cd omega-frontend
npm install
cd ..

# 3. Build Frontend
echo "🏗️ Building Frontend..."
cd omega-frontend
npm run build
cd ..

# 4. Restart Services
echo "♻️ Restarting Omega services..."
./start_all.sh

echo "✅ Update Complete!"
