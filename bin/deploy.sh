#!/bin/bash
# Pre-Deploy-Gate: pytest MUSS grün sein, sonst kein Railway-Deploy.
# Verhindert dass kaputter Code live geht weil jemand "schnell" deployen wollte.
# Usage: ./bin/deploy.sh
set -e

cd "$(dirname "$0")/.."

echo "==> 1/3 pytest"
if ! .venv/bin/pytest -q; then
  echo ""
  echo "❌ pytest FAILED — Deploy abgebrochen."
  echo "Fix die Tests, dann nochmal."
  exit 1
fi

echo ""
echo "==> 2/3 git status check"
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠️  Uncommitted changes vorhanden:"
  git status --short
  echo ""
  read -p "Trotzdem deployen? [y/N] " confirm
  if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Deploy abgebrochen."
    exit 1
  fi
fi

echo ""
echo "==> 3/3 railway up"
railway up --detach

echo ""
echo "✅ Deploy gestartet. Build-Status mit: railway logs --build"
echo "   Live-Check (sollte HTTP 302 sein): curl -I https://prozess-uebertrag-production.up.railway.app/"
