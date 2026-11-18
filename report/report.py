"""TokenWarden "AI Cost & Risk Snapshot" report: reads the database, writes HTML + PDF.

  python report.py --client "Acme BV" --out /reports        (DATABASE_URL must be set)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import pathlib

import psycopg

CSS = """
@page { size: A4; margin: 18mm 16mm; @bottom-right { content: "TokenWarden · page " counter(page); font-size: 9pt; color: #64748b; } }
body { font-family: "DejaVu Sans", Helvetica, Arial, sans-serif; color: #0f172a; font-size: 10.5pt; line-height: 1.45; }
h1 { font-size: 24pt; margin: 0 0 4pt; color: #0b1f3a; }
h2 { font-size: 14pt; color: #123c6e; border-bottom: 2px solid #dbe4f0; padding-bottom: 3pt; margin-top: 20pt; }
.sub { color: #475569; margin-bottom: 14pt; }
.kpis { display: flex; gap: 8pt; margin: 10pt 0; }
.kpi { flex: 1; border: 1px solid #dbe4f0; border-radius: 6pt; padding: 8pt 10pt; }
.kpi b { display: block; font-size: 18pt; color: #0b1f3a; }
.kpi span { color: #64748b; font-size: 9pt; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0 10pt; font-size: 9.5pt; }
th { text-align: left; background: #f1f5f9; padding: 5pt; }
td { padding: 5pt; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
.num { text-align: right; white-space: nowrap; }
.sev { font-weight: bold; color: #fff; border-radius: 3pt; padding: 1pt 5pt; font-size: 8.5pt; }
.critical { background: #7f1d1d; } .high { background: #dc2626; } .medium { background: #ea580c; } .low { background: #ca8a04; }
.bar { background: #e2e8f0; height: 7pt; border-radius: 4pt; width: 120pt; display: inline-block; vertical-align: middle; }
.bar i { display: block; height: 7pt; border-radius: 4pt; }
.note { background: #f8fafc; border-left: 3pt solid #1a73b8; padding: 6pt 9pt; color: #334155; }
.demo { background: #fff7ed; border-left: 3pt solid #ea580c; padding: 6pt 9pt; color: #9a3412; margin-bottom: 10pt; }
"""


def q(cur, sql, *args):
    cur.execute(sql, args)
    return cur.fetchall()


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def money(x) -> str:
    return f"${float(x or 0):,.2f}"


def build(conn, client: str) -> str:
    with conn.cursor() as cur:
        demo = q(cur, "SELECT bool_or(demo) FROM ai_usage WHERE ts >= now() - interval '30 days'")[0][0]
        spend30 = q(cur, "SELECT coalesce(sum(cost_usd),0), coalesce(sum(input_tokens+output_tokens),0) "
                         "FROM ai_usage WHERE ts >= now() - interval '30 days'")[0]
        budgets = q(cur, "SELECT team, budget_usd, spent_usd, burn_pct FROM team_budget_mtd ORDER BY burn_pct DESC NULLS LAST")
        by_team = q(cur, "SELECT team, round(sum(cost_usd),2), sum(input_tokens+output_tokens) FROM ai_usage "
                         "WHERE ts >= now() - interval '30 days' GROUP BY team ORDER BY 2 DESC")
        by_model = q(cur, "SELECT provider, model, round(sum(cost_usd),2) FROM ai_usage "
                          "WHERE ts >= now() - interval '30 days' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10")
        waste = q(cur, "SELECT u.team, round(sum(u.cost_usd),2), round(sum(u.cost_usd) - sum(u.input_tokens/1e6*h.input_per_mtok"
                       " + u.output_tokens/1e6*h.output_per_mtok),2) FROM ai_usage u JOIN model_prices h ON h.model='claude-haiku'"
                       " WHERE u.ts >= now() - interval '30 days' AND u.model LIKE 'claude-opus%%' GROUP BY 1 ORDER BY 3 DESC")
        scan = q(cur, "SELECT id, name, score, ts FROM latest_scan")
        findings, assets = [], []
        if scan:
            findings = q(cur, "SELECT severity, owasp, owasp_name, title, file, line, detail FROM ai_findings WHERE scan_id=%s "
                              "ORDER BY array_position(ARRAY['critical','high','medium','low'], severity)", scan[0][0])
            assets = q(cur, "SELECT kind, count(*) FROM ai_assets WHERE scan_id=%s GROUP BY kind", scan[0][0])

    total, tokens = spend30
    over = [b for b in budgets if b[3] is not None and b[3] >= 80]
    saving = sum(float(w[2]) for w in waste)
    score = scan[0][2] if scan else None
    assets_d = dict(assets)
    today = dt.date.today().strftime("%d %B %Y")

    parts = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>",
             f"<h1>AI Cost &amp; Risk Snapshot</h1><div class='sub'>{esc(client)} · {today} · prepared with TokenWarden</div>"]
    if demo:
        parts.append("<div class='demo'><b>Sample report.</b> This report contains generated demo data, not real usage.</div>")

    parts.append("<h2>1. Summary</h2><div class='kpis'>"
                 f"<div class='kpi'><b>{money(total)}</b><span>AI spend, last 30 days</span></div>"
                 f"<div class='kpi'><b>{len(over)}</b><span>teams over 80% of budget</span></div>"
                 f"<div class='kpi'><b>{money(saving)}</b><span>possible saving (premium → small model)</span></div>"
                 f"<div class='kpi'><b>{score if score is not None else '–'}/100</b><span>AI security score</span></div></div>")
    top = []
    if over:
        top.append(f"{len(over)} team(s) are at or above 80% of their monthly AI budget: "
                   + ", ".join(f"{esc(b[0])} ({b[3]}%)" for b in over) + ".")
    if saving > 0:
        top.append(f"Premium-model usage could be cut by about {money(saving)} per 30 days by routing simple, "
                   "high-volume work (classification, routing, short answers) to a small model.")
    crit = [f for f in findings if f[0] in ("critical", "high")]
    if crit:
        top.append(f"{len(crit)} critical/high security finding(s) need action this week. Start with the top of the table in section 4.")
    parts.append("<div class='note'><b>Top actions</b><ol>" + "".join(f"<li>{t}</li>" for t in top) + "</ol></div>")

    parts.append("<h2>2. Spend and budgets by team</h2><table><tr><th>Team</th><th class='num'>Spend (30d)</th>"
                 "<th class='num'>Tokens (30d)</th><th class='num'>Budget</th><th>Month-to-date burn</th></tr>")
    bmap = {b[0]: b for b in budgets}
    for team, cost, toks in by_team:
        b = bmap.get(team)
        pct = float(b[3]) if b and b[3] is not None else 0
        color = "#dc2626" if pct >= 100 else "#ea580c" if pct >= 80 else "#16a34a"
        parts.append(f"<tr><td>{esc(team)}</td><td class='num'>{money(cost)}</td><td class='num'>{int(toks or 0):,}</td>"
                     f"<td class='num'>{money(b[1]) if b else '–'}</td><td><span class='bar'><i style='width:{min(pct,100):.0f}%;"
                     f"background:{color}'></i></span> {pct:.0f}%</td></tr>")
    parts.append("</table><h2>3. Where the money goes: top models</h2><table><tr><th>Provider</th><th>Model</th>"
                 "<th class='num'>Spend (30d)</th></tr>")
    parts += [f"<tr><td>{esc(p)}</td><td>{esc(m)}</td><td class='num'>{money(c)}</td></tr>" for p, m, c in by_model]
    parts.append("</table>")

    parts.append("<h2>4. AI security findings</h2>")
    if scan:
        parts.append(f"<p>Scan <b>{esc(scan[0][1])}</b>: score <b>{score}/100</b> · AI apps: {assets_d.get('ai-app', 0)} · "
                     f"MCP servers: {assets_d.get('mcp-server', 0)} · exposed keys: {assets_d.get('api-key', 0)}. "
                     "Findings are mapped to the OWASP Top 10 for LLM Applications.</p>")
        parts.append("<table><tr><th>Severity</th><th>OWASP</th><th>Finding</th><th>Where</th><th>How to fix</th></tr>")
        for sev, ow, own, title, f, line, detail in findings:
            where = f"{f}:{line}" if line else f
            parts.append(f"<tr><td><span class='sev {sev}'>{sev}</span></td><td>{ow}<br><small>{esc(own)}</small></td>"
                         f"<td>{esc(title)}</td><td><small>{esc(where)}</small></td><td><small>{esc(detail)}</small></td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>No security scan has been run yet.</p>")

    parts.append("<h2>5. Recommended next steps</h2><ol>"
                 "<li><b>This week:</b> rotate any exposed keys and remove shell or filesystem-wide access from AI agents.</li>"
                 "<li><b>This month:</b> give every team its own key and budget through a central AI gateway, with alerts at 80%.</li>"
                 "<li><b>Ongoing:</b> re-scan monthly and review new agents and MCP servers before they go live.</li></ol>"
                 "<p class='sub'>Method: read-only access to provider usage/cost APIs and a read-only code scan. "
                 "No production systems were changed.</p></body></html>")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="Sample Client")
    ap.add_argument("--out", default="/reports")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        doc = build(conn, args.client)
    stem = f"ai-cost-risk-snapshot-{dt.date.today():%Y-%m-%d}"
    (out / f"{stem}.html").write_text(doc)
    from weasyprint import HTML
    HTML(string=doc).write_pdf(out / f"{stem}.pdf")
    print(f"wrote {out / stem}.pdf and .html")


if __name__ == "__main__":
    main()
