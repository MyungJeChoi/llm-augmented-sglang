"""Milestone C evaluation harness (no browser).

What this checks
- /health responds
- /chat/query executes NL2SQL and returns rows
- /agent/chat supports (a) direct NL2SQL, (b) clarification(pending) then follow-up, (c) rank follow-up
- request_id propagation (response header X-Request-ID == JSON request_id)
- audit_logs persistence in SQLite for NL2SQL executions
- /metrics counters move as expected

Run
  cd api
  python tools/eval_milestone_c.py --host 127.0.0.1 --port 8001

Notes
- Metrics are in-memory; restart -> reset.
- Query-level cache can short-circuit NL2SQL and skip audit logging.
  This script adds unique tags to avoid query-cache hits.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


# ---- repo import plumbing (so this works from anywhere) ----
REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.core.config import settings  # noqa: E402


def _resolve_sqlite_path(sqlalchemy_url: str) -> Path:
    """Resolve sqlite:///../data/app.db the same way runtime does."""
    if not sqlalchemy_url.startswith("sqlite"):
        raise ValueError("This eval harness expects SQLite (no-docker scaffold).")

    if sqlalchemy_url.startswith("sqlite:////"):
        return Path(sqlalchemy_url.replace("sqlite:////", "/"))

    if sqlalchemy_url.startswith("sqlite:///"):
        rel = sqlalchemy_url.replace("sqlite:///", "")
        # runtime resolves relative to API_DIR (because uvicorn is typically run from ./api)
        return (API_DIR / rel).resolve()

    raise ValueError(f"Unsupported SQLite URL format: {sqlalchemy_url}")


def _db_connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _audit_count(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) AS n FROM audit_logs").fetchone()[0])


def _audit_find_by_request_id(con: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT audit_id, created_at, actor, request_id, action, payload
        FROM audit_logs
        WHERE request_id = ?
        ORDER BY audit_id DESC
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()
    if not row:
        return None

    out = dict(row)
    try:
        out["payload"] = json.loads(out.get("payload") or "{}")
    except Exception:
        # keep raw payload if JSON parsing fails
        pass
    return out


def _audit_count_by_request_id(con: sqlite3.Connection, request_id: str) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return int(row[0] if row else 0)


def _http_json(method: str, url: str, *, json_body: dict | None = None, headers: dict | None = None, timeout: int = 60):
    r = requests.request(method, url, json=json_body, headers=headers or {}, timeout=timeout)
    rid = r.headers.get("X-Request-ID")
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    return r.status_code, rid, data


def _metrics_snapshot(base: str) -> dict:
    code, _, data = _http_json("GET", f"{base}/metrics", timeout=30)
    if code != 200:
        raise RuntimeError(f"/metrics returned {code}: {data}")
    return data


def _counter(snapshot: dict, key: str) -> int:
    return int((snapshot.get("counters") or {}).get(key, 0))


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--actor", default="me")
    ap.add_argument("--sleep", type=float, default=0.0, help="Optional sleep between calls (sec)")
    ap.add_argument("--show_audit_tail", action="store_true", help="Print last 10 audit logs at the end")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    run_tag = uuid.uuid4().hex[:8]

    db_path = _resolve_sqlite_path(settings.sqlalchemy_database_url)
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path} (did you run scripts/init_sqlite.py?)")

    con = _db_connect(db_path)

    print("[eval] base_url:", base)
    print("[eval] sqlite_db:", db_path)
    print("[eval] enable_cache:", settings.enable_cache)

    # --- snapshots before ---
    metrics0 = _metrics_snapshot(base)
    audit0 = _audit_count(con)

    print("[eval] metrics(before): http_requests_total=", _counter(metrics0, "http_requests_total"),
          "nl2sql_requests_total=", _counter(metrics0, "nl2sql_requests_total"),
          "nl2sql_cache_hit_total=", _counter(metrics0, "nl2sql_cache_hit_total"))
    print("[eval] audit_logs(before):", audit0)

    results: list[CaseResult] = []

    def record(name: str, ok: bool, detail: str):
        results.append(CaseResult(name, ok, detail))
        print(("[PASS]" if ok else "[FAIL]"), name, "-", detail)

    def sleep_if_needed():
        if args.sleep > 0:
            time.sleep(args.sleep)

    def cache_hit_counter() -> int:
        snap = _metrics_snapshot(base)
        return _counter(snap, "nl2sql_cache_hit_total")

    # Case 0: /health
    code, rid, data = _http_json("GET", f"{base}/health", timeout=10)
    record("health", code == 200 and data.get("status") == "ok", f"status={code}, rid={rid}, body={data}")
    sleep_if_needed()

    # Case 1: /chat/query (downtime)
    q1 = f"정지시간 많은 장비 top10 [{run_tag}-A]"
    cache_before = cache_hit_counter()
    code, rid, data = _http_json(
        "POST",
        f"{base}/chat/query",
        json_body={"query": q1, "actor": args.actor},
        timeout=120,
    )
    ok = (code == 200 and data.get("ok") is True and isinstance(data.get("rows"), list) and data.get("rows"))
    ok = ok and ("downtime_hours" in (data.get("rows")[0] or {}))
    ok = ok and (rid is not None and data.get("request_id") == rid)
    record("chat_query_downtime", ok, f"status={code}, rid={rid}, rows={len(data.get('rows') or [])}")

    # audit check for this request
    if rid:
        a = _audit_find_by_request_id(con, rid)
        cache_after = cache_hit_counter()
        cache_hit = cache_after > cache_before
        ok_a = (a is not None and a.get("action") == "nl2sql.query" and (a.get("payload") or {}).get("request_id") == rid) or cache_hit
        record(
            "audit_for_chat_query_downtime",
            ok_a,
            f"action={(a or {}).get('action')}, audit_id={(a or {}).get('audit_id')}, cache_hit={cache_hit}",
        )
    sleep_if_needed()

    # Case 2: /chat/query (temperature)
    q2 = f"온도 높은 장비 top10 [{run_tag}-B]"
    cache_before = cache_hit_counter()
    code, rid, data = _http_json(
        "POST",
        f"{base}/chat/query",
        json_body={"query": q2, "actor": args.actor},
        timeout=120,
    )
    ok = (code == 200 and data.get("ok") is True and isinstance(data.get("rows"), list) and data.get("rows"))
    ok = ok and ("avg_temp" in (data.get("rows")[0] or {}))
    ok = ok and (rid is not None and data.get("request_id") == rid)
    record("chat_query_temperature", ok, f"status={code}, rid={rid}, rows={len(data.get('rows') or [])}")

    if rid:
        a = _audit_find_by_request_id(con, rid)
        cache_after = cache_hit_counter()
        cache_hit = cache_after > cache_before
        ok_a = (a is not None and a.get("action") == "nl2sql.query") or cache_hit
        record(
            "audit_for_chat_query_temperature",
            ok_a,
            f"action={(a or {}).get('action')}, audit_id={(a or {}).get('audit_id')}, cache_hit={cache_hit}",
        )
    sleep_if_needed()

    # Case 3: /agent/chat direct NL2SQL
    msg3 = f"정지시간 많은 장비 top10 [{run_tag}-C]"
    cache_before = cache_hit_counter()
    code, rid, data = _http_json(
        "POST",
        f"{base}/agent/chat",
        json_body={"message": msg3, "actor": args.actor, "debug": True},
        timeout=180,
    )
    ok = code == 200 and data.get("ok") is True and data.get("thread_id")
    ok = ok and data.get("last_nl2sql_ok") is True and data.get("pending") in (None, {})
    ok = ok and (rid is not None and data.get("request_id") == rid)
    record("agent_direct", ok, f"status={code}, rid={rid}, thread_id={data.get('thread_id')}")

    if rid:
        a = _audit_find_by_request_id(con, rid)
        cache_after = cache_hit_counter()
        cache_hit = cache_after > cache_before
        ok_a = (a is not None and a.get("action") == "nl2sql.query") or cache_hit
        record(
            "audit_for_agent_direct",
            ok_a,
            f"action={(a or {}).get('action')}, audit_id={(a or {}).get('audit_id')}, cache_hit={cache_hit}",
        )
    sleep_if_needed()

    # Case 4: /agent/chat clarification -> pending
    msg4 = f"장비 top10 [{run_tag}-D]"  # no metric keyword -> should clarify
    code, rid4, data4 = _http_json(
        "POST",
        f"{base}/agent/chat",
        json_body={"message": msg4, "actor": args.actor, "debug": True},
        timeout=180,
    )
    thread_id = data4.get("thread_id")
    pending = data4.get("pending")

    ok = code == 200 and data4.get("ok") is True and thread_id
    ok = ok and isinstance(pending, dict) and pending.get("reason") == "need_metric"
    # clarify path should NOT have last_nl2sql_ok True
    ok = ok and (data4.get("last_nl2sql_ok") in (None, False))
    ok = ok and (rid4 is not None and data4.get("request_id") == rid4)
    record("agent_clarify_first_turn", ok, f"status={code}, rid={rid4}, thread_id={thread_id}, pending={pending}")

    # audit should NOT be created for clarify-only turn
    if rid4:
        cnt = _audit_count_by_request_id(con, rid4)
        record("audit_absent_for_clarify_turn", cnt == 0, f"audit_rows_with_request_id={cnt}")
    sleep_if_needed()

    # Case 5: follow-up to clarification -> should run
    cache_before = cache_hit_counter()
    code, rid5, data5 = _http_json(
        "POST",
        f"{base}/agent/chat",
        json_body={"message": "다운타임", "thread_id": thread_id, "actor": args.actor, "debug": True},
        timeout=180,
    )
    ok = code == 200 and data5.get("ok") is True and data5.get("thread_id") == thread_id
    ok = ok and data5.get("last_nl2sql_ok") is True and data5.get("pending") in (None, {})
    ok = ok and "다운타임" in (data5.get("current_query") or "")
    ok = ok and (rid5 is not None and data5.get("request_id") == rid5)
    record("agent_clarify_followup_runs", ok, f"status={code}, rid={rid5}, current_query={data5.get('current_query')}")

    if rid5:
        a = _audit_find_by_request_id(con, rid5)
        cache_after = cache_hit_counter()
        cache_hit = cache_after > cache_before
        ok_a = (a is not None and a.get("action") == "nl2sql.query") or cache_hit
        record(
            "audit_for_followup_runs",
            ok_a,
            f"action={(a or {}).get('action')}, audit_id={(a or {}).get('audit_id')}, cache_hit={cache_hit}",
        )
    sleep_if_needed()

    # Case 6: rank follow-up (1등 장비 이벤트)
    cache_before = cache_hit_counter()
    code, rid6, data6 = _http_json(
        "POST",
        f"{base}/agent/chat",
        json_body={"message": "1등 장비 이벤트 보여줘", "thread_id": thread_id, "actor": args.actor, "debug": True},
        timeout=180,
    )
    last = data6.get("last_nl2sql") or {}
    sql = (last.get("sql") or "")
    ok = code == 200 and data6.get("ok") is True and data6.get("thread_id") == thread_id
    ok = ok and data6.get("last_nl2sql_ok") is True
    ok = ok and ("from events" in sql.lower())
    ok = ok and (rid6 is not None and data6.get("request_id") == rid6)
    record("agent_rank_followup_events", ok, f"status={code}, rid={rid6}, current_query={data6.get('current_query')}")

    if rid6:
        a = _audit_find_by_request_id(con, rid6)
        cache_after = cache_hit_counter()
        cache_hit = cache_after > cache_before
        ok_a = (a is not None and a.get("action") == "nl2sql.query") or cache_hit
        record(
            "audit_for_rank_followup",
            ok_a,
            f"action={(a or {}).get('action')}, audit_id={(a or {}).get('audit_id')}, cache_hit={cache_hit}",
        )

    # --- snapshots after ---
    metrics1 = _metrics_snapshot(base)
    audit1 = _audit_count(con)

    # Diffs (best-effort; other traffic can affect these)
    diff_http = _counter(metrics1, "http_requests_total") - _counter(metrics0, "http_requests_total")
    diff_nl2sql = _counter(metrics1, "nl2sql_requests_total") - _counter(metrics0, "nl2sql_requests_total")
    diff_cache_hit = _counter(metrics1, "nl2sql_cache_hit_total") - _counter(metrics0, "nl2sql_cache_hit_total")
    diff_audit = audit1 - audit0

    print("\n[eval] metrics(after): http_requests_total=", _counter(metrics1, "http_requests_total"),
          "nl2sql_requests_total=", _counter(metrics1, "nl2sql_requests_total"),
          "nl2sql_cache_hit_total=", _counter(metrics1, "nl2sql_cache_hit_total"))
    print("[eval] metrics(diff): http=", diff_http, "nl2sql=", diff_nl2sql, "cache_hit=", diff_cache_hit)
    print("[eval] audit_logs(after):", audit1, "diff=", diff_audit)

    # Summary
    n_pass = sum(1 for r in results if r.ok)
    n_total = len(results)
    print(f"\n[summary] {n_pass}/{n_total} checks passed")

    if args.show_audit_tail:
        print("\n[audit_tail] last 10")
        rows = con.execute(
            "SELECT audit_id, created_at, action, actor, request_id FROM audit_logs ORDER BY audit_id DESC LIMIT 10"
        ).fetchall()
        for r in rows:
            print(dict(r))

    # Exit code
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
