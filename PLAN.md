# TokenWarden: build plan

Status 2026-11-09: Milestone 1 is DONE and tested on Colima. `docker compose up -d` works, the dashboard renders, and alerts fire for Data Science (89%) and Customer Support (84%) and get delivered to the alert-sink webhook. The LiteLLM gateway profile is not tested yet (it needs real keys).
Milestone 2 DONE 2026-11-09: collectors/collect.py covers Anthropic, OpenAI and Azure. Mock-mode test passed: correct totals, idempotent re-runs, unmapped spend goes to Unassigned, missing keys are skipped cleanly. NOT yet tested against live accounts. The OpenAI and Azure response shapes come from memory and need checking on the first real run.

Concept: ../ai-governance-bundle.html (cost + security service, 3 phases).
Goal: a demo you can show a CTO in 2 minutes, plus a repeatable Phase 1 "Snapshot" delivery.

Milestone 3 (core) DONE 2026-11-09: scanner/scan.py and the "AI Risk" dashboard. The demo repo scored 23/100 with 8 expected findings. Scanning ~/VoiceClaude gave 0 false positives. Still open: IAM checks and promptfoo tests (M3b).

## Milestone 1: Cost demo (weekend 1)
- [ ] `docker-compose.yml`: LiteLLM proxy, PostgreSQL, Prometheus, Grafana
- [ ] LiteLLM config: 3 fake teams (Platform, Data, Support), each with a virtual key + monthly budget
- [ ] `seed/` script that generates 30 days of realistic sample spend, so no real API cost
- [ ] Grafana dashboard "AI Spend": per team, per model, trend, budget burn %, top 10 costly calls
- [ ] Alert rule: team over 80% of budget → Slack/Teams webhook (demo webhook)

## Milestone 2: No-install collectors (weekend 1–2)
- [ ] `collectors/anthropic.py`: Usage & Cost Admin API → Postgres (workspace → team map)
- [ ] `collectors/openai.py`: Usage/Costs API → Postgres (project → team map)
- [ ] `collectors/azure_openai.py`: Azure Cost Management query → Postgres (subscription/tag → team)
- [ ] `teams.yaml`: one mapping file the client fills in
- [ ] All collectors read-only, with mock mode for the demo

## Milestone 3: Risk scanner (weekend 2)
- [ ] `scanner/inventory.py`: list AI keys, agents and MCP servers found in cloud and repos
- [ ] `scanner/secrets`: gitleaks wrapper, so leaked AI API keys get flagged
- [ ] `scanner/iam.py`: flag over-broad roles on identities used by agents (Azure first, then AWS)
- [ ] `scanner/mcp_check.py`: flag MCP configs with write/exec tools, plain-text tokens, no allow-list
- [ ] `scanner/promptfoo/`: prompt-injection test suite for one sample chatbot
- [ ] Findings scored against OWASP Top 10 for LLM Apps → Postgres → Grafana "AI Risk" dashboard

## Milestone 4: Deliverables (week 3)
- [ ] PDF report template "AI Cost & Risk Snapshot" with sample data
- [ ] Helm chart + Terraform module for Phase 2 (AKS first)
- [ ] README with architecture diagram, 2-minute demo video, public GitHub repo
- [ ] Upwork catalog offer + LinkedIn post + 20 DMs to CTOs/platform leads

## Rules
- Phase 1 is strictly read-only; put that in the SOW.
- Demo uses fake data only; never real client keys in the repo.
- Build Milestones 3–4 deeper only after 2–3 prospects say "show me".
