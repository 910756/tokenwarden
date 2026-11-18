"""TokenWarden risk scanner: finds AI-related security risks in a codebase (read-only).

Checks
  * leaked AI provider keys in files               -> OWASP LLM02 Sensitive Information Disclosure
  * MCP server configs: shell/exec/filesystem-root -> LLM06 Excessive Agency
                        plain-text tokens          -> LLM02
                        unpinned npx/uvx packages  -> LLM03 Supply Chain
                        remote server over http:// -> LLM03
  * LLM output passed to eval/exec/shell           -> LLM05 Improper Output Handling
  * inventory of AI apps (files using LLM SDKs) and MCP servers

Usage
  python scan.py --target /path/to/repos [--name "Client X"] [--db]     (--db writes to Postgres via DATABASE_URL)
  python scan.py --demo                                                 (scan a generated vulnerable demo repo)
Secrets are never stored or printed in full; only a redacted fingerprint is kept.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".terraform", ".next"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".env", ".ini", ".cfg", ".tf",
            ".tfvars", ".sh", ".md", ".txt", ".ipynb", ".java", ".go", ".rb", ".cs", ".properties", ".conf"}
MAX_BYTES = 2_000_000

SEVERITY_POINTS = {"critical": 25, "high": 10, "medium": 4, "low": 1}
OWASP = {
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Supply Chain",
    "LLM05": "Improper Output Handling",
    "LLM06": "Excessive Agency",
}

KEY_PATTERNS = [
    ("Anthropic API key", re.compile(r"sk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}T3BlbkFJ[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"sk-proj-[A-Za-z0-9_\-]{40,}")),
    ("Google AI API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Hugging Face token", re.compile(r"hf_[A-Za-z0-9]{34,}")),
    ("Azure OpenAI key", re.compile(r"(?i)(?:azure_openai_(?:api_)?key|AZURE_OPENAI_KEY)\s*[=:]\s*[\"']?([0-9a-f]{32})\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
]
PLACEHOLDER = re.compile(r"(?i)(x{6,}|your[_-]?key|changeme|change-me|example|placeholder|<|\$\{|os\.environ)")

LLM_SDK = re.compile(r"(?m)^\s*(?:from|import)\s+(anthropic|openai|langchain\w*|llama_index|litellm|google\.generativeai)"
                     r"|require\(['\"](@anthropic-ai/sdk|openai|langchain)['\"]\)"
                     r"|from\s+['\"](@anthropic-ai/sdk|openai|@langchain/[\w-]+)['\"]")
DANGEROUS_SINK = re.compile(r"\b(eval|exec)\s*\(|subprocess\.(?:run|call|Popen|check_output)\([^)]*shell\s*=\s*True"
                            r"|os\.system\s*\(|child_process\.exec\s*\(")
LLM_OUTPUT_HINT = re.compile(r"(?i)\b(completion|response|message|output|result|content)\b")

MCP_FILE_NAMES = {"mcp.json", ".mcp.json", "claude_desktop_config.json", "mcp_config.json"}
SHELL_HINTS = re.compile(r"(?i)(shell|exec|terminal|command|bash|powershell|run[-_]?cmd|desktop-commander)")
FS_ROOT_ARGS = {"/", "~", "C:\\", "/home", "/Users", "$HOME"}
SECRET_ENV = re.compile(r"(?i)(token|secret|password|api[_-]?key|pat)$")


def redact(secret: str) -> str:
    return f"{secret[:6]}…{secret[-4:]} (sha256:{hashlib.sha256(secret.encode()).hexdigest()[:10]})"


class Report:
    def __init__(self, target: str, name: str):
        self.target, self.name = target, name
        self.findings: list[dict] = []
        self.assets: list[dict] = []

    def finding(self, severity, owasp, title, path, line, detail, asset=""):
        self.findings.append({"severity": severity, "owasp": owasp, "title": title, "file": path,
                              "line": line, "detail": detail, "asset": asset})

    def asset(self, kind, name, location):
        self.assets.append({"kind": kind, "name": name, "location": location})

    @property
    def score(self) -> int:
        return max(0, 100 - sum(SEVERITY_POINTS[f["severity"]] for f in self.findings))


def iter_files(root: pathlib.Path):
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and (p.suffix.lower() in TEXT_EXT or p.name.startswith(".env") or p.name in MCP_FILE_NAMES):
            try:
                if p.stat().st_size <= MAX_BYTES:
                    yield p
            except OSError:
                continue


def scan_secrets(rel: str, text: str, rep: Report) -> None:
    seen = set()
    for label, rx in KEY_PATTERNS:
        for m in rx.finditer(text):
            secret = m.group(1) if m.groups() else m.group(0)
            line_text = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end())]
            if PLACEHOLDER.search(secret) or secret in seen:
                continue
            seen.add(secret)
            line = text.count("\n", 0, m.start()) + 1
            sev = "critical" if not rel.lower().endswith((".md", ".txt")) else "high"
            rep.finding(sev, "LLM02", f"{label} committed to source", rel, line,
                        f"Value {redact(secret)}. Rotate the key, remove it from history, load it from a secret store.",
                        asset=label)
            rep.asset("api-key", label, f"{rel}:{line}")
            del line_text


def scan_code(rel: str, text: str, rep: Report) -> None:
    m = LLM_SDK.search(text)
    if not m:
        return
    sdk = next(g for g in m.groups() if g)
    rep.asset("ai-app", sdk, rel)
    for sink in DANGEROUS_SINK.finditer(text):
        line_no = text.count("\n", 0, sink.start()) + 1
        line = text.splitlines()[line_no - 1]
        if LLM_OUTPUT_HINT.search(line):
            rep.finding("high", "LLM05", "LLM output may reach eval/exec/shell", rel, line_no,
                        f"`{line.strip()[:120]}`. Never execute model output directly; parse it into an allow-listed action.")


def scan_mcp(rel: str, text: str, rep: Report) -> None:
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError:
        return
    servers = cfg.get("mcpServers") or cfg.get("servers") or {}
    if not isinstance(servers, dict):
        return
    for name, s in servers.items():
        if not isinstance(s, dict):
            continue
        cmd = str(s.get("command", ""))
        args = [str(a) for a in s.get("args", [])]
        url = str(s.get("url", ""))
        rep.asset("mcp-server", name, rel)
        blob = " ".join([name, cmd] + args)
        if SHELL_HINTS.search(blob):
            rep.finding("high", "LLM06", f"MCP server '{name}' can run shell commands", rel, 0,
                        "An agent with shell access can do anything the user can. Restrict to an allow-list or remove.",
                        asset=name)
        if "filesystem" in blob.lower() and any(a.rstrip("/") in FS_ROOT_ARGS or a == "/" for a in args):
            rep.finding("high", "LLM06", f"MCP server '{name}' has filesystem access to a root/home directory", rel, 0,
                        f"Args {args}. Scope it to a single project folder.", asset=name)
        for k, v in (s.get("env") or {}).items():
            if SECRET_ENV.search(k) and isinstance(v, str) and v and not PLACEHOLDER.search(v):
                rep.finding("high", "LLM02", f"MCP server '{name}' has a plain-text secret in {k}", rel, 0,
                            f"Value {redact(v)}. Reference an env var or secret manager instead.", asset=name)
        if cmd in ("npx", "uvx", "bunx", "pipx"):
            pkgs = [a for a in args if not a.startswith("-")]
            if pkgs and not re.search(r"@\d|==\d", pkgs[0]):
                rep.finding("medium", "LLM03", f"MCP server '{name}' runs an unpinned package ({pkgs[0]})", rel, 0,
                            "Each start pulls whatever version is latest. Pin an exact version and review updates.",
                            asset=name)
        if url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1)", url):
            rep.finding("medium", "LLM03", f"MCP server '{name}' uses unencrypted http", rel, 0,
                        f"{url}. Use https and authentication.", asset=name)


def scan(target: pathlib.Path, name: str) -> Report:
    rep = Report(str(target), name)
    for p in iter_files(target):
        rel = str(p.relative_to(target))
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        scan_secrets(rel, text, rep)
        if p.name in MCP_FILE_NAMES or p.parent.name in (".cursor", ".vscode", ".claude"):
            scan_mcp(rel, text, rep)
        if p.suffix in (".py", ".js", ".ts", ".tsx", ".jsx"):
            scan_code(rel, text, rep)
    return rep


def save(rep: Report) -> int:
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO ai_scans (name, target, score) VALUES (%s,%s,%s) RETURNING id",
                    (rep.name, rep.target, rep.score))
        scan_id = cur.fetchone()[0]
        for f in rep.findings:
            cur.execute("INSERT INTO ai_findings (scan_id, severity, owasp, owasp_name, title, file, line, detail, asset)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (scan_id, f["severity"], f["owasp"], OWASP[f["owasp"]], f["title"], f["file"], f["line"],
                         f["detail"], f["asset"]))
        for a in rep.assets:
            cur.execute("INSERT INTO ai_assets (scan_id, kind, name, location) VALUES (%s,%s,%s,%s)",
                        (scan_id, a["kind"], a["name"], a["location"]))
        conn.commit()
    return scan_id


def make_demo_repo() -> pathlib.Path:
    """Builds a small deliberately-vulnerable repo. Keys are random fakes generated at runtime (never committed)."""
    import secrets
    root = pathlib.Path(tempfile.mkdtemp(prefix="tw-demo-"))
    fake_ant = "sk-ant-api03-" + secrets.token_urlsafe(70)[:93]
    fake_gh = "ghp_" + secrets.token_hex(18)
    (root / "support-bot").mkdir()
    (root / "support-bot" / "bot.py").write_text(
        "import anthropic, subprocess\n"
        f"client = anthropic.Anthropic(api_key=\"{fake_ant}\")\n"
        "def handle(ticket):\n"
        "    response = client.messages.create(model='claude-opus-5-5', max_tokens=500,\n"
        "        messages=[{'role': 'user', 'content': ticket}])\n"
        "    action = response.content[0].text\n"
        "    subprocess.run(action, shell=True)  # runs whatever the model output says\n")
    (root / "analytics").mkdir()
    (root / "analytics" / "summarise.py").write_text(
        "from openai import OpenAI\nimport os\nclient = OpenAI(api_key=os.environ['OPENAI_API_KEY'])\n")
    (root / ".cursor").mkdir()
    (root / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "desktop-commander": {"command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander"]},
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@2025.1.14", "/Users"]},
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": fake_gh}},
        "crm": {"url": "http://crm-mcp.internal.example:8080/sse"},
    }}, indent=2))
    (root / "README.md").write_text("# Demo services\nSet OPENAI_API_KEY=your-key-here before running.\n")
    return root


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="/scan")
    ap.add_argument("--name", default="scan")
    ap.add_argument("--demo", action="store_true", help="scan a generated vulnerable demo repo")
    ap.add_argument("--db", action="store_true", help="store results in Postgres (DATABASE_URL)")
    ap.add_argument("--json", action="store_true", help="print full JSON report")
    args = ap.parse_args()

    target = make_demo_repo() if args.demo else pathlib.Path(args.target)
    name = "Demo: vulnerable AI services" if args.demo else args.name
    if not target.is_dir():
        sys.exit(f"target {target} is not a directory")
    rep = scan(target, name)

    if args.json:
        print(json.dumps({"name": rep.name, "score": rep.score, "findings": rep.findings, "assets": rep.assets}, indent=2))
    else:
        print(f"TokenWarden AI risk scan: {name}\nScore: {rep.score}/100  |  findings: {len(rep.findings)}  |  "
              f"AI apps: {sum(a['kind']=='ai-app' for a in rep.assets)}  |  "
              f"MCP servers: {sum(a['kind']=='mcp-server' for a in rep.assets)}")
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for f in sorted(rep.findings, key=lambda f: order[f["severity"]]):
            loc = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
            print(f"  [{f['severity'].upper():8}] {f['owasp']} {f['title']}  ({loc})")
    if args.db:
        print(f"saved as scan #{save(rep)}")


if __name__ == "__main__":
    main()
