-- TokenWarden risk scanner tables
CREATE TABLE IF NOT EXISTS ai_scans (
  id        serial PRIMARY KEY,
  ts        timestamptz NOT NULL DEFAULT now(),
  name      text NOT NULL,
  target    text NOT NULL,
  score     int NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_findings (
  id          serial PRIMARY KEY,
  scan_id     int NOT NULL REFERENCES ai_scans(id) ON DELETE CASCADE,
  severity    text NOT NULL CHECK (severity IN ('critical','high','medium','low')),
  owasp       text NOT NULL,
  owasp_name  text NOT NULL,
  title       text NOT NULL,
  file        text,
  line        int,
  detail      text,
  asset       text
);

CREATE TABLE IF NOT EXISTS ai_assets (
  id        serial PRIMARY KEY,
  scan_id   int NOT NULL REFERENCES ai_scans(id) ON DELETE CASCADE,
  kind      text NOT NULL,          -- ai-app | mcp-server | api-key
  name      text NOT NULL,
  location  text
);

CREATE OR REPLACE VIEW latest_scan AS
SELECT * FROM ai_scans ORDER BY ts DESC, id DESC LIMIT 1;
