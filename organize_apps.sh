#!/bin/bash
# Moves every Django app into an  apps/  folder.   Run from the project root (where manage.py is):   bash organize_apps.sh
# Close runserver and VS Code terminals that use the project first (Windows locks open folders).
set -e
[ -f manage.py ] || { echo "Run this in the folder that has manage.py"; exit 1; }
[ -f config/settings.py ] || { echo "config/settings.py not found"; exit 1; }

APPS="accounts pos masteradmin suppliers importer pricelist sku_manager"
# compiled files do not belong in git; untrack any that slipped in (they stay ignored afterwards)
git ls-files | grep -E "__pycache__|\.pyc$" | xargs -r -d '\n' git rm --cached -q 2>/dev/null || true
mkdir -p apps
for a in $APPS; do
  [ -d "$a" ] || { echo "skip $a (not here)"; continue; }
  find "$a" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  mv "$a" apps/
  echo "moved $a -> apps/$a"
done
git add -A apps 2>/dev/null || true        # git records these as moves, history is kept
git add -u 2>/dev/null || true

# tell Python where the apps are; app names stay 'accounts', 'pos', ... so no import, migration or URL changes
if ! grep -q "BASE_DIR / 'apps'" config/settings.py; then
  cp config/settings.py config/settings.py.bak
  if grep -q "^BASE_DIR *=" config/settings.py; then
    sed -i "/^BASE_DIR *=/a\\
\\
import sys\\
sys.path.insert(0, str(BASE_DIR / 'apps'))   # all Django apps live in the apps/ folder" config/settings.py
    echo "settings.py updated (backup: config/settings.py.bak)"
  else
    echo "!! Could not find the BASE_DIR line. Add these 2 lines right after it in config/settings.py:"
    echo "   import sys"; echo "   sys.path.insert(0, str(BASE_DIR / 'apps'))"
  fi
fi

echo "--- paths in settings.py that still mention an app folder (fix by hand if any) ---"
grep -nE "BASE_DIR *[/,].*(sku_manager|accounts|pos|masteradmin|suppliers|importer|pricelist)" config/settings.py || echo "none"

echo "--- checking ---"
python manage.py check
python manage.py makemigrations --check --dry-run
echo "All good. Test the site, then:  git add -A && git commit -m 'Move apps into apps/'"
