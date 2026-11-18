#!/usr/bin/env bash
# Creates demo teams with monthly budgets and one virtual key each on the LiteLLM gateway.
# Usage: LITELLM_MASTER_KEY=... ./scripts/create-teams.sh   (gateway must be running on :4000)
set -euo pipefail
GW="${GW:-http://localhost:4000}"
KEY="${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY}"

create_team () {
  local alias="$1" budget="$2"
  team_id=$(curl -sf "$GW/team/new" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"team_alias\":\"$alias\",\"max_budget\":$budget,\"budget_duration\":\"30d\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["team_id"])')
  curl -sf "$GW/key/generate" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"team_id\":\"$team_id\",\"key_alias\":\"$alias-key\"}" >/dev/null
  echo "created team '$alias' (budget \$$budget / 30d) with key alias '$alias-key'"
}

create_team platform 600
create_team data-science 850
create_team customer-support 400
create_team marketing 300
