#!/usr/bin/env bash
# Live test of the Anthropic collector against your own org.
# Prompts for the Admin API key without echoing it; the key is never written to disk.
# Needs: docker compose stack running (postgres on 127.0.0.1:5432), python3 with collectors/requirements.txt.
set -euo pipefail
cd "$(dirname "$0")/../collectors"

read -rsp "Anthropic Admin API key (sk-ant-admin...): " ANTHROPIC_ADMIN_KEY; echo
export ANTHROPIC_ADMIN_KEY
export DATABASE_URL="postgresql://tokenwarden:${POSTGRES_PASSWORD:-demo-only-change-me}@127.0.0.1:5432/tokenwarden"

python3 collect.py --providers anthropic --days 30
echo "Done. Check the AI Spend dashboard at http://localhost:3000"
