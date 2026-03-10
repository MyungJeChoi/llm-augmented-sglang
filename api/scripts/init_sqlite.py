"""Initialize the local SQLite DB for the scaffold.

- Creates ./data/app.db (at repo root) by default
- Applies schema
- Inserts small seed data so /chat/query demos work
- Ensures kv_store has a kg_version (for cache invalidation)

Run from anywhere:
    python api/scripts/init_sqlite.py

It reads config from repo_root/.env via app.core.config.Settings.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Ensure we can import 'app' when running from repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
sys.path.insert(0, str(API_DIR))

from app.core.config import settings  # noqa: E402


def _resolve_sqlite_path(sqlalchemy_url: str) -> Path:
    # expected format: sqlite:///../data/app.db  (relative to api/) or sqlite:////abs/path/app.db
    if not sqlalchemy_url.startswith("sqlite"):
        raise ValueError("SQLALCHEMY_DATABASE_URL must start with sqlite for this no-docker setup.")
    # Parse the path part manually (keep it simple)
    if sqlalchemy_url.startswith("sqlite:////"):
        return Path(sqlalchemy_url.replace("sqlite:////", "/"))
    if sqlalchemy_url.startswith("sqlite:///"):
        rel = sqlalchemy_url.replace("sqlite:///", "")
        # If user runs uvicorn from ./api, the path is relative to ./api.
        # Here we resolve relative to API_DIR to match runtime behavior.
        return (API_DIR / rel).resolve()
    raise ValueError(f"Unsupported SQLite URL format: {sqlalchemy_url}")


def main():
    db_path = _resolve_sqlite_path(settings.sqlalchemy_database_url)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = REPO_ROOT / "db" / "sqlite" / "sqlite_schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    print(f"[init_sqlite] creating db at: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)

        # Ensure kv_store has a kg_version (used for cache invalidation)
        cur = conn.execute("SELECT value FROM kv_store WHERE key='kg_version';")
        row = cur.fetchone()
        if row is None:
            ver = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO kv_store(key, value) VALUES (?, ?)",
                ("kg_version", ver),
            )
            conn.commit()
            print(f"[init_sqlite] set initial kg_version={ver}")

        # Idempotent seed: check if already seeded
        cur = conn.execute("SELECT COUNT(*) FROM orgs;")
        if cur.fetchone()[0] > 0:
            print("[init_sqlite] seed already exists; skipping inserts.")
            return

        # Seed data (simple asset ops domain)
        conn.execute("INSERT INTO orgs(org_name, parent_org_id) VALUES (?, ?)", ("HQ", None))
        conn.execute("INSERT INTO orgs(org_name, parent_org_id) VALUES (?, ?)", ("Manufacturing", 1))
        conn.execute("INSERT INTO orgs(org_name, parent_org_id) VALUES (?, ?)", ("R&D", 1))

        conn.execute(
            "INSERT INTO assets(asset_type, asset_name, org_id, location, tags) VALUES (?, ?, ?, ?, ?)",
            ("equipment", "ETCH-01", 2, "LINE-A", '{"model":"E100","vendor":"ACME"}'),
        )
        conn.execute(
            "INSERT INTO assets(asset_type, asset_name, org_id, location, tags) VALUES (?, ?, ?, ?, ?)",
            ("equipment", "ETCH-02", 2, "LINE-A", '{"model":"E100","vendor":"ACME"}'),
        )
        conn.execute(
            "INSERT INTO assets(asset_type, asset_name, org_id, location, tags) VALUES (?, ?, ?, ?, ?)",
            ("vehicle", "CAR-01", 1, "FLEET", '{"model":"S1"}'),
        )

        now = datetime.utcnow()

        def ts(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        # Events
        conn.execute(
            "INSERT INTO events(asset_id, event_type, start_ts, end_ts, severity, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                "downtime",
                ts(now - timedelta(days=3)),
                ts(now - timedelta(days=3) + timedelta(hours=2)),
                2,
                '{"cause":"pump"}',
            ),
        )
        conn.execute(
            "INSERT INTO events(asset_id, event_type, start_ts, end_ts, severity, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                "maintenance",
                ts(now - timedelta(days=2)),
                ts(now - timedelta(days=2) + timedelta(hours=1)),
                1,
                '{"work_order":"WO-100"}',
            ),
        )
        conn.execute(
            "INSERT INTO events(asset_id, event_type, start_ts, end_ts, severity, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (
                2,
                "downtime",
                ts(now - timedelta(days=1)),
                ts(now - timedelta(days=1) + timedelta(minutes=30)),
                1,
                '{"cause":"sensor"}',
            ),
        )
        conn.execute(
            "INSERT INTO events(asset_id, event_type, start_ts, end_ts, severity, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (
                3,
                "alert",
                ts(now - timedelta(hours=12)),
                ts(now - timedelta(hours=12) + timedelta(minutes=5)),
                3,
                '{"type":"lane_departure"}',
            ),
        )

        # Metrics
        conn.execute(
            "INSERT INTO metrics(asset_id, ts, metric_name, value) VALUES (?, ?, ?, ?)",
            (1, ts(now - timedelta(days=3)), "temperature", 80.0),
        )
        conn.execute(
            "INSERT INTO metrics(asset_id, ts, metric_name, value) VALUES (?, ?, ?, ?)",
            (1, ts(now - timedelta(days=2)), "temperature", 85.0),
        )
        conn.execute(
            "INSERT INTO metrics(asset_id, ts, metric_name, value) VALUES (?, ?, ?, ?)",
            (3, ts(now - timedelta(hours=12)), "fatigue_score", 0.72),
        )

        conn.commit()
        print("[init_sqlite] schema + seed done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
