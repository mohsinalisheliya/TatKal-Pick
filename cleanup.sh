#!/bin/bash
# Run from the project root (the folder with manage.py) in Git Bash:   bash cleanup.sh
set -e
echo "--- will delete ---"
ls -d pos/templates/pos/prices.html templates/sku_manager/login.html media/imports 2>/dev/null || true
read -p "Delete these? (y/n) " a
[ "$a" = "y" ] || exit 0
rm -f  pos/templates/pos/prices.html templates/sku_manager/login.html
rm -rf media/imports
echo "--- stop git tracking things that must not be on GitHub (files stay on your PC) ---"
git rm -r --cached --ignore-unmatch -q .conda media db.sqlite3 .env
git status --short | head -20
echo "Done. Now: copy .gitignore.txt to .gitignore, then  git add .gitignore && git commit -m 'Clean up, ignore secrets'"
