# TokenWarden

[![CI](https://github.com/910756/tokenwarden/actions/workflows/ci.yml/badge.svg)](https://github.com/910756/tokenwarden/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**See who's spending your AI budget, and where your AI is risky.**

TokenWarden is a vendor-neutral kit for AI cost and security governance. It shows AI spend per team, model and API key, flags waste, and warns before budgets run out. It also scans AI agents, keys and MCP servers for risk, scored against the OWASP LLM Top 10.

> Status: **Milestones 1–3 done** (cost demo, read-only collectors, risk scanner). See [PLAN.md](PLAN.md) and [CHANGELOG.md](CHANGELOG.md).

## Quick start (demo, fake data, no API keys needed)

```bash
docker compose up -d
open http://localhost:3000     # login admin / admin, then change it
```

The **TokenWarden · AI Spend** dashboard shows:

- spend this month, and how many teams are over 80% of their budget
- budget burn per team (orange at 80%, red at 100%)
- daily spend by team, with a planted anomaly: Data Science triples its Opus usage in the last 3 days
- spend by model
- **Waste finder**: premium-model usage and what it would cost on a small model
- spend by API key, split by source (gateway vs. provider usage API)

The data is 30 days of generated usage for 4 fake teams. Prices in `db/init.sql` are illustrative demo numbers, not official provider pricing.

## Read-only collectors (Phase 1, no install at the client)

These pull real usage and cost from provider admin APIs into the same database:

| Provider | API | Team mapping | Credential (read-only) |
|---|---|---|---|
| Anthropic | Usage & Cost Admin API (`usage_report/messages`, `cost_report`) | workspace → team | Admin API key |
| OpenAI | Organization Usage + Costs API | project → team | Org admin key |
| Azure OpenAI | Cost Management Query API | resource group → team | Service principal with *Cost Management Reader* |

```bash
cp collectors/teams.example.yaml collectors/teams.yaml    # map the client's workspaces/projects/RGs to teams
docker compose run --rm collector --mock                  # try it with bundled sample responses, no keys
docker compose --profile collectors up -d collector       # real run, every hour (keys in .env)
```

Anything unmapped lands in an `Unassigned` team, so no spend goes missing. Re-runs are idempotent. Demo rows are flagged `demo=true`, and you can wipe them before a real engagement with `DELETE FROM ai_usage WHERE demo;`.

## Optional: the real gateway (Phase 2)

```bash
cp .env.example .env            # add your own keys
docker compose --profile gateway up -d
./scripts/create-teams.sh       # teams + budgets + one virtual key each
```

Apps then point at `http://<gateway>:4000` with their team key. That's a one-line change per app.

## How it's delivered

| Phase | What the client installs | What they get |
|---|---|---|
| 1. Snapshot | Nothing: read-only admin keys + read-only cloud role | Spend per team, AI key/agent inventory, risk report |
| 2. Control | One central gateway (Helm/Terraform) | Hard budgets, guardrails, audit log |
| 3. Watch | – | Monthly cost & risk report |

## Roadmap
- [x] M1: Docker Compose demo, seeded data, Grafana dashboard
- [x] M1: budget alerts at 80% and 100%, per team, sent to a webhook (demo sink, or Slack/Teams via `ALERT_WEBHOOK_URL`)
- [x] M2: read-only collectors for Anthropic, OpenAI and Azure usage/cost APIs (tested with sample responses; not yet against live accounts)
- [x] M3: risk scanner: leaked AI keys, MCP configs (shell/filesystem access, plain-text secrets, unpinned packages, http), LLM output → eval/exec. Scored against OWASP LLM Top 10, with an "AI Risk" dashboard. Try it: `docker compose run --rm scanner --demo --db`
- [ ] M3b: cloud IAM checks for agent identities, and promptfoo prompt-injection tests
- [ ] M4: PDF report, Helm chart + Terraform module, demo video

## Contributing & security
See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Licensed under [MIT](LICENSE).

Built by Sanjay Singh, Senior DevOps Engineer · github.com/910756
