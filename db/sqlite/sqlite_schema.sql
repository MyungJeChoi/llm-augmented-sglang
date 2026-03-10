-- sqlite_schema.sql : Minimal structured DB schema (SQLite)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS orgs (
  org_id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_name TEXT NOT NULL,
  parent_org_id INTEGER NULL REFERENCES orgs(org_id)
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_type TEXT NOT NULL,
  asset_name TEXT NOT NULL,
  org_id INTEGER NOT NULL REFERENCES orgs(org_id),
  location TEXT NULL,
  tags TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL REFERENCES assets(asset_id),
  event_type TEXT NOT NULL,
  start_ts TEXT NOT NULL,
  end_ts TEXT NULL,
  severity INTEGER DEFAULT 0,
  metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL REFERENCES assets(asset_id),
  ts TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  value REAL NOT NULL
);

-- Observability / Audit
CREATE TABLE IF NOT EXISTS audit_logs (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NULL,
  request_id TEXT NULL,
  action TEXT NOT NULL,
  payload TEXT DEFAULT '{}'
);

-- KG governance
CREATE TABLE IF NOT EXISTS kv_store (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_changes (
  change_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NULL,
  request_id TEXT NULL,
  action TEXT NOT NULL,
  before_payload TEXT NULL,
  after_payload TEXT NULL,
  reason TEXT NULL,
  source_type TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_asset_time ON events(asset_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_metrics_asset_time ON metrics(asset_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_action_time ON audit_logs(action, created_at);
CREATE INDEX IF NOT EXISTS idx_kg_changes_action_time ON kg_changes(action, created_at);
