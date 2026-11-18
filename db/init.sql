-- TokenWarden demo schema + 30 days of FAKE usage data.
-- Prices below are illustrative demo numbers, not official provider pricing.

CREATE DATABASE litellm;  -- used only by the optional LiteLLM gateway

CREATE TABLE teams (
  name                text PRIMARY KEY,
  monthly_budget_usd  numeric NOT NULL
);

CREATE TABLE model_prices (
  model             text PRIMARY KEY,
  provider          text NOT NULL,
  input_per_mtok    numeric NOT NULL,   -- USD per 1M input tokens (demo)
  output_per_mtok   numeric NOT NULL    -- USD per 1M output tokens (demo)
);

CREATE TABLE ai_usage (
  ts             timestamptz NOT NULL,
  team           text NOT NULL REFERENCES teams(name),
  provider       text NOT NULL,
  model          text NOT NULL,
  api_key_alias  text NOT NULL,
  requests       int NOT NULL,
  input_tokens   bigint NOT NULL,
  output_tokens  bigint NOT NULL,
  cost_usd       numeric(12,4) NOT NULL,
  source         text NOT NULL,         -- 'gateway' or 'usage-api'
  demo           boolean NOT NULL DEFAULT false   -- true = generated sample data (DELETE FROM ai_usage WHERE demo)
);
CREATE INDEX ON ai_usage (ts);
CREATE INDEX ON ai_usage (team, ts);

INSERT INTO teams VALUES
  ('Platform',          600),
  ('Data Science',      850),
  ('Customer Support',  400),
  ('Marketing',         300);

INSERT INTO model_prices VALUES
  ('claude-opus',    'anthropic', 15.00, 75.00),
  ('claude-sonnet',  'anthropic',  3.00, 15.00),
  ('claude-haiku',   'anthropic',  0.80,  4.00),
  ('gpt-4o',         'openai',     2.50, 10.00),
  ('gpt-4o-mini',    'openai',     0.15,  0.60);

-- Which team uses which model, and how heavily (relative weight).
CREATE TEMP TABLE team_model_mix (team text, model text, weight numeric, source text);
INSERT INTO team_model_mix VALUES
  ('Platform',         'claude-sonnet', 1.0, 'gateway'),
  ('Platform',         'claude-haiku',  2.0, 'gateway'),
  ('Data Science',     'claude-opus',   0.5, 'gateway'),
  ('Data Science',     'claude-sonnet', 2.0, 'gateway'),
  ('Data Science',     'gpt-4o',        1.0, 'usage-api'),
  -- Story for the demo: Support classifies tickets with the most expensive model.
  ('Customer Support', 'claude-opus',   0.6, 'gateway'),
  ('Customer Support', 'gpt-4o-mini',   3.0, 'usage-api'),
  ('Marketing',        'claude-sonnet', 0.5, 'usage-api'),
  ('Marketing',        'gpt-4o',        0.5, 'usage-api');

-- One row per hour per team per model for the last 30 days.
-- Office hours are busy, nights and weekends are quiet,
-- and Data Science spikes on Opus in the last 3 days (an anomaly to find).
INSERT INTO ai_usage (ts, team, provider, model, api_key_alias, requests, input_tokens, output_tokens, cost_usd, source, demo)
SELECT h.ts, m.team, p.provider, m.model,
       lower(replace(m.team, ' ', '-')) || '-' || m.source || '-key',
       greatest(1, (x.in_tok / 4000))::int,
       x.in_tok,
       (x.in_tok * 0.25)::bigint,
       round(x.in_tok / 1e6 * p.input_per_mtok + (x.in_tok * 0.25) / 1e6 * p.output_per_mtok, 4),
       m.source,
       true
FROM generate_series(date_trunc('hour', now()) - interval '30 days',
                     date_trunc('hour', now()), interval '1 hour') AS h(ts)
CROSS JOIN team_model_mix m
JOIN model_prices p ON p.model = m.model
CROSS JOIN LATERAL (
  SELECT (CASE WHEN extract(isodow FROM h.ts) IN (6, 7) THEN 0.2 ELSE 1.0 END)
       * (CASE WHEN extract(hour FROM h.ts) BETWEEN 8 AND 18 THEN 1.0 ELSE 0.15 END)
       * (CASE WHEN m.team = 'Data Science' AND m.model = 'claude-opus'
                AND h.ts > now() - interval '3 days' THEN 4.0 ELSE 1.0 END) AS f
) fx
CROSS JOIN LATERAL (
  SELECT (m.weight * 200000 * fx.f * (0.5 + random()))::bigint AS in_tok
) x
WHERE x.in_tok > 0;

-- Month-to-date budget burn per team (what the alert and gauge use).
CREATE VIEW team_budget_mtd AS
SELECT t.name AS team,
       t.monthly_budget_usd AS budget_usd,
       coalesce(sum(u.cost_usd), 0) AS spent_usd,
       round(100 * coalesce(sum(u.cost_usd), 0) / nullif(t.monthly_budget_usd, 0), 1) AS burn_pct
FROM teams t
LEFT JOIN ai_usage u ON u.team = t.name AND u.ts >= date_trunc('month', now())
GROUP BY t.name, t.monthly_budget_usd;
