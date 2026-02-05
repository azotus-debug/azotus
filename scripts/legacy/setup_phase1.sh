#!/bin/bash
set -e

echo "🚀 STAYING ALIVE: Starting Omega Copilot Phase 1 Setup..."

# 0. Ensure Node.js is ready
export PATH="/opt/homebrew/bin:$PATH"

if ! command -v node &> /dev/null; then
  echo "🔧 Node.js not found. Installing via Homebrew..."
  brew install node
else
  echo "✅ Node.js detected: $(node -v)"
fi

# 1. Archive Old Dashboard
echo "📦 Archiving old templates..."
mkdir -p _archive/templates
mv templates/*.html _archive/templates/ 2>/dev/null || echo "No templates to move."
mv static _archive/static 2>/dev/null || echo "No static files to move."
echo "✅ Archived."

# 2. Initialize Next.js Frontend
echo "⚛️ Creating Next.js App (omega-frontend)..."
# Using non-interactive flags for create-next-app
npx -y create-next-app@latest omega-frontend \
  --typescript \
  --tailwind \
  --eslint \
  --app \
  --src-dir \
  --import-alias "@/*" \
  --use-npm \
  --no-git \
  --yes

echo "✅ Next.js App Created."

# 3. Install Additional UI Libraries (Radix UI / Lucide)
echo "🎨 Installing UI Dependencies..."
cd omega-frontend
npm install lucide-react clsx tailwind-merge framer-motion

echo "🎉 PHASE 1 SETUP COMPLETE."
echo "You can now run: cd omega-frontend && npm run dev"
