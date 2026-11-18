#!/bin/bash
# Pushes TokenWarden to github.com/910756/tokenwarden (public).
set -e
cd "$(dirname "$0")"
grep -qx '.env' .gitignore 2>/dev/null || echo '.env' >> .gitignore
[ -d .git ] || git init -b main
git add -A
git diff --cached --name-only | grep -E '(^|/)\.env$' && { echo "Refusing: .env staged"; exit 1; }
git commit -m "TokenWarden: AI spend and security guard" || true
if git remote | grep -q origin; then
  git push -u origin main
else
  gh repo create 910756/tokenwarden --public --source=. --push \
    --description "Track and cap AI token spend per team, with security guardrails"
fi
echo "Live at https://github.com/910756/tokenwarden"
