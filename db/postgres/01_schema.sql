-- 01_schema.sql : Minimal structured DB schema (generic "asset ops" domain)

CREATE TABLE IF NOT EXISTS orgs (
  org_id SERIAL PRIMARY KEY,
  org_name TEXT NOT NULL,
  parent_org_id INT NULL REFERENCES orgs(org_id)
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id SERIAL PRIMARY KEY,
  asset_type TEXT NOT NULL,            -- e.g., equipment, vehicle
  asset_name TEXT NOT NULL,
  org_id INT NOT NULL REFERENCES orgs(org_id),
  location TEXT NULL,
  tags JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS events (
  event_id SERIAL PRIMARY KEY,
  asset_id INT NOT NULL REFERENCES assets(asset_id),
  event_type TEXT NOT NULL,            -- e.g., downtime, maintenance, alert
  start_ts TIMESTAMPTZ NOT NULL,
  end_ts TIMESTAMPTZ NULL,
  severity INT DEFAULT 0,
  metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS metrics (
  metric_id SERIAL PRIMARY KEY,
  asset_id INT NOT NULL REFERENCES assets(asset_id),
  ts TIMESTAMPTZ NOT NULL,
  metric_name TEXT NOT NULL,           -- e.g., temperature, fatigue_score
  value DOUBLE PRECISION NOT NULL
);

-- Simple audit table (for change history / trace)
CREATE TABLE IF NOT EXISTS audit_logs (
  audit_id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor TEXT NULL,
  request_id TEXT NULL,
  action TEXT NOT NULL,
  payload JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_events_asset_time ON events(asset_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_metrics_asset_time ON metrics(asset_id, ts);
