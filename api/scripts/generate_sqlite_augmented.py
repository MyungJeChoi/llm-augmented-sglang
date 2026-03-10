"""Generate a larger deterministic SQLite dataset for Milestone D scale evaluation.

This script creates a new SQLite DB (default: ../data/app_large.db) using the same schema as toy data,
then inserts:
- orgs
- assets (ETCH-001..)
- events (downtime/maintenance/alert)
- metrics (temperature)

It also ensures kv_store contains kg_version.

Usage:
  cd api
  python scripts/generate_sqlite_augmented.py --db ../data/app_large.db --assets 200 --events-per-asset 80 --metrics-per-asset 200 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
SCHEMA_SQL = REPO_ROOT / "db" / "sqlite" / "sqlite_schema.sql"


def _resolve_db_path(db: str) -> Path:
    p = Path(db)
    if not p.is_absolute():
        p = (API_DIR / db).resolve()
    return p


def _executescript(con: sqlite3.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    con.executescript(sql)


def _create_scale_indexes(con: sqlite3.Connection) -> None:
    # Extra indexes for scale tests (safe if they already exist)
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_assets_name ON assets(asset_name);
        CREATE INDEX IF NOT EXISTS idx_events_type_asset_end ON events(event_type, asset_id, end_ts);
        CREATE INDEX IF NOT EXISTS idx_metrics_name_asset_ts ON metrics(metric_name, asset_id, ts);
        """
    )


def _set_kv(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO kv_store(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
        (key, value),
    )


def _ensure_txn_ready(con: sqlite3.Connection) -> None:
    """SQLite disallows nested BEGIN; ensure we start with a clean transaction state."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="../data/app_large.db", help="Path to SQLite DB (relative to ./api)")
    ap.add_argument("--assets", type=int, default=200)
    ap.add_argument("--events-per-asset", type=int, default=80)
    ap.add_argument("--metrics-per-asset", type=int, default=200)
    ap.add_argument("--days", type=int, default=14, help="Span of timestamps (days back from now)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--overwrite", action="store_true", help="Delete DB file if exists")
    ap.add_argument("--no-extra-indexes", action="store_true", help="Do not create extra scale indexes")
    args = ap.parse_args()

    db_path = _resolve_db_path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        if args.overwrite:
            db_path.unlink()
        else:
            print(f"[gen] DB already exists: {db_path} (use --overwrite to recreate)")
            return 2

    if not SCHEMA_SQL.exists():
        print(f"[gen] schema not found: {SCHEMA_SQL}")
        return 3

    rng = random.Random(args.seed)

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys=ON;")

    print(f"[gen] creating schema: {SCHEMA_SQL}")
    _executescript(con, SCHEMA_SQL)

    if not args.no_extra_indexes:
        _create_scale_indexes(con)

    # --- orgs ---
    con.execute("INSERT INTO orgs(org_name, parent_org_id) VALUES(?, NULL)", ("FAB",))
    org_id = int(con.execute("SELECT org_id FROM orgs WHERE org_name='FAB'").fetchone()[0])

    # --- assets ---
    print(f"[gen] inserting assets: {args.assets}")
    assets = []
    for i in range(1, args.assets + 1):
        asset_type = rng.choice(["ETCH", "CMP", "CVD", "DIFF"])
        asset_name = f"{asset_type}-{i:03d}"
        location = rng.choice(["BAY-1", "BAY-2", "BAY-3", "BAY-4"])
        tags = json.dumps({"line": rng.choice(["L1", "L2"]), "model": rng.choice(["M1", "M2", "M3"])})
        con.execute(
            "INSERT INTO assets(asset_type, asset_name, org_id, location, tags) VALUES(?, ?, ?, ?, ?)",
            (asset_type, asset_name, org_id, location, tags),
        )
        asset_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        assets.append((asset_id, asset_name, asset_type))

    # --- events + metrics ---
    print(f"[gen] inserting events: {args.events_per_asset} per asset")
    now = datetime.utcnow()
    start_base = now - timedelta(days=args.days)

    # Use transaction batches for speed
    t0 = time.time()
    _ensure_txn_ready(con)
    ev_count = 0
    for asset_id, asset_name, asset_type in assets:
        last_t = start_base
        for _ in range(args.events_per_asset):
            # spacing 0~12 hours
            last_t = last_t + timedelta(hours=rng.random() * 12)
            event_type = rng.choices(["downtime", "maintenance", "alert"], weights=[0.25, 0.15, 0.60])[0]
            severity = int(rng.choices([0, 1, 2, 3], weights=[0.55, 0.25, 0.15, 0.05])[0])
            dur_min = rng.randint(5, 120) if event_type == "downtime" else rng.randint(1, 30)
            end_t = last_t + timedelta(minutes=dur_min) if rng.random() < 0.95 else None  # allow some open events

            con.execute(
                "INSERT INTO events(asset_id, event_type, start_ts, end_ts, severity, metadata) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    asset_id,
                    event_type,
                    last_t.strftime("%Y-%m-%d %H:%M:%S"),
                    end_t.strftime("%Y-%m-%d %H:%M:%S") if end_t else None,
                    severity,
                    json.dumps({"source": "synthetic", "seed": args.seed}),
                ),
            )
            ev_count += 1
    con.execute("COMMIT")
    print(f"[gen] events inserted: {ev_count} in {time.time()-t0:.2f}s")

    print(f"[gen] inserting metrics: {args.metrics_per_asset} per asset")
    t0 = time.time()
    _ensure_txn_ready(con)
    mt_count = 0
    for asset_id, asset_name, asset_type in assets:
        # metric series over days
        for j in range(args.metrics_per_asset):
            # random timestamp in window
            dt = start_base + timedelta(seconds=rng.random() * args.days * 86400)
            # temperature distribution by type
            base = {"ETCH": 65, "CMP": 55, "CVD": 75, "DIFF": 85}.get(asset_type, 60)
            temp = rng.gauss(mu=base, sigma=3.5)
            con.execute(
                "INSERT INTO metrics(asset_id, ts, metric_name, value) VALUES(?, ?, ?, ?)",
                (asset_id, dt.strftime("%Y-%m-%d %H:%M:%S"), "temperature", float(temp)),
            )
            mt_count += 1
    con.execute("COMMIT")
    print(f"[gen] metrics inserted: {mt_count} in {time.time()-t0:.2f}s")

    # --- kv_store: kg_version ---
    _set_kv(con, "kg_version", str(uuid.uuid4()))
    con.commit()

    # Summary counts
    assets_n = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    events_n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    metrics_n = con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    print(f"[gen] done: {db_path}")
    print(f"[gen] counts: assets={assets_n}, events={events_n}, metrics={metrics_n}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
