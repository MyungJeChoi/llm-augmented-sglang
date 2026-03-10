"""Export audit_logs from SQLite to JSONL (for building eval suites).

Usage:
  cd api
  python tools/export_audit_jsonl.py --out ../eval/from_audit.jsonl --limit 200 --action nl2sql.query
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.core.config import settings  # noqa: E402


def _resolve_sqlite_path(sqlalchemy_url: str) -> Path:
    if not sqlalchemy_url.startswith("sqlite"):
        raise ValueError("This exporter expects SQLite.")
    if sqlalchemy_url.startswith("sqlite:////"):
        return Path(sqlalchemy_url.replace("sqlite:////", "/"))
    if sqlalchemy_url.startswith("sqlite:///"):
        rel = sqlalchemy_url.replace("sqlite:///", "")
        return (API_DIR / rel).resolve()
    raise ValueError(f"Unsupported SQLite URL: {sqlalchemy_url}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--action", default=None, help="Filter by action (e.g., nl2sql.query)")
    ap.add_argument("--sqlite-db", default=None)
    args = ap.parse_args()

    db_path = Path(args.sqlite_db).resolve() if args.sqlite_db else _resolve_sqlite_path(settings.sqlalchemy_database_url)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    q = "SELECT audit_id, created_at, actor, request_id, action, payload FROM audit_logs"
    params: list[Any] = []
    if args.action:
        q += " WHERE action = ?"
        params.append(args.action)
    q += " ORDER BY audit_id DESC LIMIT ?"
    params.append(args.limit)

    rows = con.execute(q, params).fetchall()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        for r in rows[::-1]:  # chronological
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except Exception:
                pass
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    print(f"[export] wrote {len(rows)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
