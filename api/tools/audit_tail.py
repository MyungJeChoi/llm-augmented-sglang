"""Tail audit_logs from the SQLite DB (Milestone B/C).

Run
  cd api
  python tools/audit_tail.py --limit 20

Optional
  python tools/audit_tail.py --action nl2sql.query
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.core.config import settings  # noqa: E402


def _resolve_sqlite_path(sqlalchemy_url: str) -> Path:
    if sqlalchemy_url.startswith("sqlite:////"):
        return Path(sqlalchemy_url.replace("sqlite:////", "/"))
    if sqlalchemy_url.startswith("sqlite:///"):
        rel = sqlalchemy_url.replace("sqlite:///", "")
        return (API_DIR / rel).resolve()
    raise ValueError(f"Unsupported SQLite URL: {sqlalchemy_url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--action", default=None, help="Filter by exact action (e.g., nl2sql.query)")
    args = ap.parse_args()

    db_path = _resolve_sqlite_path(settings.sqlalchemy_database_url)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    where = ""
    params = []
    if args.action:
        where = "WHERE action = ?"
        params = [args.action]

    rows = con.execute(
        f"""
        SELECT audit_id, created_at, actor, request_id, action, payload
        FROM audit_logs
        {where}
        ORDER BY audit_id DESC
        LIMIT ?
        """,
        (*params, args.limit),
    ).fetchall()

    for r in rows:
        d = dict(r)
        payload = d.get("payload") or "{}"
        try:
            d["payload"] = json.loads(payload)
        except Exception:
            d["payload"] = payload
        # print compact
        print(json.dumps(d, ensure_ascii=False))


if __name__ == "__main__":
    main()
