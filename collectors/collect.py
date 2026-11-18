"""TokenWarden collectors: pull AI usage + cost from provider admin APIs (read-only) into Postgres.

Usage:
  python collect.py --providers anthropic,openai,azure --days 30
  python collect.py --mock            # use bundled fixtures, no keys needed
  python collect.py --loop 3600       # run every hour

Env:
  DATABASE_URL            postgresql://user:pass@host:5432/tokenwarden
  TEAMS_FILE              path to teams.yaml (default ./teams.yaml)
  ANTHROPIC_ADMIN_KEY     Anthropic Admin API key (sk-ant-admin01-...), read access to usage/cost reports
  OPENAI_ADMIN_KEY        OpenAI organization admin key
  AZURE_SUBSCRIPTION_ID   Azure subscription to query (Cost Management Reader is enough)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
from collections import defaultdict

import psycopg
import requests
import yaml

HERE = pathlib.Path(__file__).parent
UTC = dt.timezone.utc
TIMEOUT = 60
USER_AGENT = "TokenWarden/0.2 (https://github.com/910756/tokenwarden)"


# ---------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"[{dt.datetime.now(UTC):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


class Rows:
    """Aggregates (day, team, provider, model, key) -> tokens + cost, so usage and cost reports can be merged."""

    def __init__(self) -> None:
        self.data = defaultdict(lambda: {"requests": 0, "input": 0, "output": 0, "cost": 0.0})

    def add(self, day, team, provider, model, key, requests=0, inp=0, out=0, cost=0.0):
        r = self.data[(day, team, provider, model or "unknown", key)]
        r["requests"] += int(requests or 0)
        r["input"] += int(inp or 0)
        r["output"] += int(out or 0)
        r["cost"] += float(cost or 0)

    def __len__(self):
        return len(self.data)


class MissingSetting(Exception):
    pass


def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSetting(name)
    return value


def load_teams() -> dict:
    path = pathlib.Path(os.environ.get("TEAMS_FILE", HERE / "teams.yaml"))
    if not path.exists():
        path = HERE / "teams.example.yaml"
        log(f"teams.yaml not found, using {path.name}")
    return yaml.safe_load(path.read_text()) or {}


def team_for(mapping: dict, key, default: str) -> str:
    if key is None:
        key = "default"
    return mapping.get(key, default)


def paged_get(url: str, headers: dict, params: list[tuple]) -> list[dict]:
    """GET with has_more/next_page pagination (Anthropic + OpenAI share this shape)."""
    out, page = [], None
    while True:
        p = list(params) + ([("page", page)] if page else [])
        r = requests.get(url, headers=headers, params=p, timeout=TIMEOUT)
        if r.status_code >= 400:
            raise RuntimeError(f"{url} -> HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        out.extend(body.get("data", []))
        if not body.get("has_more"):
            return out
        page = body.get("next_page")


def fixture(name: str) -> dict:
    """Load a fixture and shift its placeholder dates ({d0}=today, {d1}=yesterday, ...) to real dates."""
    text = (HERE / "fixtures" / name).read_text()
    today = dt.datetime.now(UTC).date()
    for i in range(8):
        d = today - dt.timedelta(days=i)
        text = text.replace(f"{{d{i}}}", d.isoformat())
        text = text.replace(f"{{u{i}}}", str(int(dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())))
        text = text.replace(f"{{i{i}}}", d.strftime("%Y%m%d"))
    return json.loads(text)


# ---------------------------------------------------------------- Anthropic

ANTHROPIC = "https://api.anthropic.com/v1/organizations"


def collect_anthropic(start: dt.datetime, end: dt.datetime, cfg: dict, unmapped: str, mock: bool) -> Rows:
    ws_map = (cfg.get("anthropic") or {}).get("workspaces", {})
    rows = Rows()
    if mock:
        usage = fixture("anthropic_usage.json")["data"]
        cost = fixture("anthropic_cost.json")["data"]
    else:
        key = need("ANTHROPIC_ADMIN_KEY")
        h = {"x-api-key": key, "anthropic-version": "2026-10-21", "User-Agent": USER_AGENT}
        base = [("starting_at", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
                ("ending_at", end.strftime("%Y-%m-%dT%H:%M:%SZ"))]
        usage = paged_get(f"{ANTHROPIC}/usage_report/messages", h,
                          base + [("bucket_width", "1d"), ("limit", "31"),
                                  ("group_by[]", "workspace_id"), ("group_by[]", "model")])
        cost = paged_get(f"{ANTHROPIC}/cost_report", h,
                         base + [("limit", "31"), ("group_by[]", "workspace_id"), ("group_by[]", "description")])

    for bucket in usage:
        day = bucket["starting_at"][:10]
        for r in bucket.get("results", []):
            team = team_for(ws_map, r.get("workspace_id"), unmapped)
            cache_create = sum((r.get("cache_creation") or {}).values())
            inp = (r.get("uncached_input_tokens") or 0) + (r.get("cache_read_input_tokens") or 0) + cache_create
            rows.add(day, team, "anthropic", r.get("model"), r.get("workspace_id") or "default-workspace",
                     inp=inp, out=r.get("output_tokens") or 0)

    for bucket in cost:
        day = bucket["starting_at"][:10]
        for r in bucket.get("results", []):
            team = team_for(ws_map, r.get("workspace_id"), unmapped)
            model = r.get("model") or r.get("description") or "other"
            cents = float(r.get("amount") or 0)          # decimal string, lowest units (cents)
            rows.add(day, team, "anthropic", model, r.get("workspace_id") or "default-workspace", cost=cents / 100)
    return rows


# ---------------------------------------------------------------- OpenAI

OPENAI = "https://api.openai.com/v1/organization"


def collect_openai(start: dt.datetime, end: dt.datetime, cfg: dict, unmapped: str, mock: bool) -> Rows:
    pr_map = (cfg.get("openai") or {}).get("projects", {})
    rows = Rows()
    if mock:
        usage = fixture("openai_usage.json")["data"]
        cost = fixture("openai_costs.json")["data"]
    else:
        h = {"Authorization": f"Bearer {need('OPENAI_ADMIN_KEY')}", "User-Agent": USER_AGENT}
        base = [("start_time", str(int(start.timestamp()))), ("end_time", str(int(end.timestamp()))),
                ("bucket_width", "1d"), ("limit", "31")]
        usage = paged_get(f"{OPENAI}/usage/completions", h,
                          base + [("group_by", "project_id"), ("group_by", "model")])
        cost = paged_get(f"{OPENAI}/costs", h,
                         base + [("group_by", "project_id"), ("group_by", "line_item")])

    for bucket in usage:
        day = dt.datetime.fromtimestamp(bucket["start_time"], UTC).date().isoformat()
        for r in bucket.get("results", []):
            team = team_for(pr_map, r.get("project_id"), unmapped)
            rows.add(day, team, "openai", r.get("model"), r.get("project_id") or "default-project",
                     requests=r.get("num_model_requests"), inp=r.get("input_tokens"), out=r.get("output_tokens"))

    for bucket in cost:
        day = dt.datetime.fromtimestamp(bucket["start_time"], UTC).date().isoformat()
        for r in bucket.get("results", []):
            team = team_for(pr_map, r.get("project_id"), unmapped)
            line = r.get("line_item") or "other"
            model = line.split(",")[0].strip()          # e.g. "gpt-4o-2024-08-06, input" -> model
            value = float((r.get("amount") or {}).get("value") or 0)   # dollars
            rows.add(day, team, "openai", model, r.get("project_id") or "default-project", cost=value)
    return rows


# ---------------------------------------------------------------- Azure OpenAI (Cost Management)

def collect_azure(start: dt.datetime, end: dt.datetime, cfg: dict, unmapped: str, mock: bool) -> Rows:
    az = cfg.get("azure") or {}
    rg_map = {k.lower(): v for k, v in (az.get("resource_groups") or {}).items()}
    services = az.get("service_names", ["Cognitive Services"])
    rows = Rows()
    if mock:
        pages = [fixture("azure_cost.json")]
    else:
        from azure.identity import DefaultAzureCredential   # only needed for real Azure runs
        sub = need("AZURE_SUBSCRIPTION_ID")
        token = DefaultAzureCredential().get_token("https://management.azure.com/.default").token
        url = (f"https://management.azure.com/subscriptions/{sub}/providers/"
               f"Microsoft.CostManagement/query?api-version=2023-11-01")
        body = {
            "type": "ActualCost", "timeframe": "Custom",
            "timePeriod": {"from": start.isoformat(), "to": end.isoformat()},
            "dataset": {
                "granularity": "Daily",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "grouping": [{"type": "Dimension", "name": "ResourceGroupName"},
                             {"type": "Dimension", "name": "Meter"}],
                "filter": {"dimensions": {"name": "ServiceName", "operator": "In", "values": services}},
            },
        }
        pages = []
        while url:
            r = requests.post(url, json=body, timeout=TIMEOUT,
                              headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
            if r.status_code >= 400:
                raise RuntimeError(f"Azure Cost Management -> HTTP {r.status_code}: {r.text[:300]}")
            page = r.json()
            pages.append(page)
            url = (page.get("properties") or {}).get("nextLink")

    for page in pages:
        props = page["properties"]
        cols = [c["name"] for c in props["columns"]]
        for row in props["rows"]:
            rec = dict(zip(cols, row))
            d = str(rec.get("UsageDate"))
            day = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            rg = (rec.get("ResourceGroupName") or "").lower()
            if rec.get("Currency") not in (None, "USD"):
                log(f"azure: cost in {rec.get('Currency')} for {rg}; stored as-is (no FX conversion yet)")
            rows.add(day, team_for(rg_map, rg, unmapped), "azure-openai", rec.get("Meter"),
                     rg or "unknown-rg", cost=rec.get("Cost") or rec.get("PreTaxCost") or 0)
    return rows


# ---------------------------------------------------------------- write

def write(conn, provider: str, rows: Rows, start: dt.datetime, end: dt.datetime, budgets: dict,
          mock: bool = False) -> None:
    with conn.cursor() as cur:
        teams = {k[1] for k in rows.data}
        for t in teams:
            cur.execute("INSERT INTO teams (name, monthly_budget_usd) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                        (t, budgets.get(t, 0)))
        # idempotent: replace this provider's previously collected rows for the window
        if mock:
            cur.execute("DELETE FROM ai_usage WHERE provider = %s AND api_key_alias LIKE 'mock:%%'", (provider,))
        else:
            cur.execute("DELETE FROM ai_usage WHERE provider = %s AND source = 'usage-api' AND NOT demo"
                        " AND ts >= %s AND ts < %s", (provider, start, end))
        for (day, team, prov, model, key), v in rows.data.items():
            cur.execute(
                "INSERT INTO ai_usage (ts, team, provider, model, api_key_alias, requests, input_tokens,"
                " output_tokens, cost_usd, source, demo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'usage-api',%s)",
                (f"{day}T00:00:00Z", team, prov, model, f"mock:{key}" if mock else key, v["requests"],
                 v["input"], v["output"], round(v["cost"], 4), mock))
    conn.commit()
    total = sum(v["cost"] for v in rows.data.values())
    log(f"{provider}: wrote {len(rows)} rows, ${total:,.2f} for {len({k[1] for k in rows.data})} team(s)")


COLLECTORS = {"anthropic": collect_anthropic, "openai": collect_openai, "azure-openai": collect_azure}
ALIASES = {"azure": "azure-openai"}


def run_once(args) -> int:
    cfg = load_teams()
    unmapped = cfg.get("unmapped_team", "Unassigned")
    budgets = cfg.get("budgets", {})
    end = dt.datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days)
    failures = 0
    with psycopg.connect(need("DATABASE_URL")) as conn:
        for name in args.providers.split(","):
            name = ALIASES.get(name.strip(), name.strip())
            try:
                rows = COLLECTORS[name](start, end, cfg, unmapped, args.mock)
                write(conn, name, rows, start, end, budgets, args.mock)
            except MissingSetting as e:
                log(f"{name}: skipped, {e} is not set")
            except Exception as e:   # one provider failing must not stop the others
                failures += 1
                conn.rollback()
                log(f"{name}: FAILED: {e}")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--providers", default="anthropic,openai,azure")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--mock", action="store_true", help="use bundled fixtures instead of real APIs")
    ap.add_argument("--loop", type=int, default=0, help="repeat every N seconds")
    args = ap.parse_args()
    if args.mock:
        args.days = min(args.days, 7)
    while True:
        failures = run_once(args)
        if not args.loop:
            sys.exit(1 if failures else 0)
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
