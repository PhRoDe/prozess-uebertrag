#!/bin/bash
# Pre-Push-Gate: pytest MUSS grün sein, sonst kein Push auf GitHub.
# Deploy läuft seit 2026-06-10 über GitHub → Webhook → Calandi-Server.
# Ein Push auf main triggert CI + Deploy-Webhook — kaputter Code darf
# also gar nicht erst gepusht werden.
# Usage: ./bin/deploy.sh
set -e

cd "$(dirname "$0")/.."

echo "==> 1/3 pytest"
if ! .venv/bin/pytest -q; then
  echo ""
  echo "❌ pytest FAILED — Push abgebrochen."
  echo "Fix die Tests, dann nochmal."
  exit 1
fi

echo ""
echo "==> 2/3 git status check"
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠️  Uncommitted changes vorhanden:"
  git status --short
  echo ""
  read -p "Ohne Commit wird nur der bestehende Stand gepusht. Fortfahren? [y/N] " confirm
  if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Push abgebrochen."
    exit 1
  fi
fi

echo ""
echo "==> 3/3 git push (triggert CI + Deploy-Webhook)"
git push

echo ""
echo "✅ Push raus. GitHub-Actions-CI läuft, danach zieht der Webhook den Stand."
echo "   Live-Check: curl https://uebertrag.calandi-tools.de/health  (erwartet {\"status\":\"ok\"})"
