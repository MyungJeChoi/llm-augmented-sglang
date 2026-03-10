"""Milestone D evaluator (two lanes): toy smoke vs augmented scale.

Design goals
- Toy smoke: fast regression, deterministic, catches wiring issues (request_id, audit, metrics, agent pending flow).
- Augmented scale: larger tables, latency distribution, cache behavior, batch asset queries.

Usage
  cd api
  # Toy smoke (default suite)
  python tools/eval_milestone_d.py --scenario toy --host 127.0.0.1 --port 8750

  # Scale (default suite) - ensure the server uses a large DB (SQLALCHEMY_DATABASE_URL)
  python tools/eval_milestone_d.py --scenario scale --host 127.0.0.1 --port 8750

Outputs
  runs/<timestamp>_<run_id>/
    - summary.json
    - report.md
    - results.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import requests

# ---- repo import plumbing ----
REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.core.config import settings  # noqa: E402


# ---------------------------
# Helpers
# ---------------------------

def _now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _resolve_sqlite_path(sqlalchemy_url: str) -> Path:
    """Resolve sqlite:///../data/app.db similar to runtime."""
    if not sqlalchemy_url.startswith("sqlite"):
        raise ValueError("Evaluator expects SQLite.")
    if sqlalchemy_url.startswith("sqlite:////"):
        return Path(sqlalchemy_url.replace("sqlite:////", "/"))
    if sqlalchemy_url.startswith("sqlite:///"):
        rel = sqlalchemy_url.replace("sqlite:///", "")
        return (API_DIR / rel).resolve()
    raise ValueError(f"Unsupported SQLite URL: {sqlalchemy_url}")


def _db_connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


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
        pass
    return out


def _audit_count_by_request_id(con: sqlite3.Connection, request_id: str) -> int:
    row = con.execute("SELECT COUNT(*) FROM audit_logs WHERE request_id=?", (request_id,)).fetchone()
    return int(row[0] if row else 0)


def _audit_total(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])


def _metrics_snapshot(base: str) -> dict:
    r = requests.get(f"{base}/metrics", timeout=30)
    r.raise_for_status()
    return r.json()


def _counter(snapshot: dict, key: str) -> int:
    return int((snapshot.get("counters") or {}).get(key, 0))


def _http_json(method: str, url: str, *, json_body: dict | None = None, timeout: int = 90) -> tuple[int, str | None, dict]:
    t0 = time.time()
    r = requests.request(method, url, json=json_body, timeout=timeout)
    elapsed_ms = (time.time() - t0) * 1000.0
    rid = r.headers.get("X-Request-ID")
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    # Attach wall time to help p95 even if pipeline timings absent
    if isinstance(data, dict):
        data.setdefault("_client_elapsed_ms", elapsed_ms)
    return r.status_code, rid, data


def _make_run_dir(out_root: Path, run_id: str | None = None) -> Path:
    run_id = run_id or uuid.uuid4().hex[:10]
    out = out_root / f"{_now_ts()}_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, obj: Any) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load_suite(path: Path) -> list[dict]:
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")
    )

    cases: list[dict] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1

        if i >= len(text):
            break

        if text[i] != "{":
            raise ValueError(
                f"Malformed suite entry at char {i}: expected JSON object start '{{' in {path}"
            )

        case, next_i = decoder.raw_decode(text, i)
        if not isinstance(case, dict):
            raise ValueError(f"Suite entry at char {i} in {path} is not an object")
        cases.append(case)
        i = next_i
    return cases


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = max(0, int(round(0.95 * (len(xs) - 1))))
    return float(xs[k])


def _db_stats(con: sqlite3.Connection) -> dict[str, int]:
    def c(t: str) -> int:
        return int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    return {"assets": c("assets"), "events": c("events"), "metrics": c("metrics"), "audit_logs": c("audit_logs")}


def _sample_assets_with_downtime(con: sqlite3.Connection, limit: int) -> list[str]:
    rows = con.execute(
        """
        SELECT a.asset_name, COUNT(*) AS n
        FROM assets a
        JOIN events e ON e.asset_id = a.asset_id
        WHERE e.event_type='downtime' AND e.end_ts IS NOT NULL
        GROUP BY a.asset_name
        HAVING n > 0
        ORDER BY n DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [str(r["asset_name"]) for r in rows]


def _sample_assets_any(con: sqlite3.Connection, limit: int) -> list[str]:
    rows = con.execute("SELECT asset_name FROM assets ORDER BY asset_id LIMIT ?", (limit,)).fetchall()
    return [str(r["asset_name"]) for r in rows]


# ---------------------------
# Evaluation core
# ---------------------------

@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    meta: dict[str, Any] | None = None


def _check_health(base_url: str, results_jsonl: Path) -> Check:
    code, rid, data = _http_json("GET", f"{base_url}/health")
    _append_jsonl(results_jsonl, {"case": "health", "status": code, "rid": rid, "resp": data})
    if code != 200:
        return Check("health", False, f"status={code}")
    status = (data or {}).get("status")
    return Check("health", status == "ok", f"status={code}, body.status={status}")


def _check_chat_query(
    base_url: str,
    con: sqlite3.Connection,
    actor: str,
    query: str,
    *,
    name: str,
    min_rows: int = 1,
    row_must_have: list[str] | None = None,
    tag_unique: bool = True,
    results_jsonl: Path,
) -> tuple[Check, dict]:
    q = query
    if tag_unique:
        q = f"{q} [{uuid.uuid4().hex[:8]}-D]"
    payload = {"query": q, "actor": actor}
    code, rid, data = _http_json("POST", f"{base_url}/chat/query", json_body=payload)
    _append_jsonl(results_jsonl, {"case": name, "endpoint": "/chat/query", "payload": payload, "status": code, "rid": rid, "resp": data})

    if code != 200 or not isinstance(data, dict):
        return Check(name, False, f"status={code}", {"rid": rid}), {}

    body_rid = data.get("request_id")
    if rid and body_rid and rid != body_rid:
        return Check(name, False, f"request_id mismatch header={rid} body={body_rid}", {"rid": rid}), data

    rows = data.get("rows") or []
    if len(rows) < min_rows:
        return Check(name, False, f"rows={len(rows)} (<{min_rows})", {"rid": rid, "sql": data.get("sql")}), data

    if row_must_have and rows:
        missing = [k for k in row_must_have if k not in rows[0]]
        if missing:
            return Check(name, False, f"row missing keys: {missing}", {"rid": rid, "row0": rows[0]}), data

    # Audit expectation: only if query_cache miss
    cache_q = ((data.get("cache") or {}).get("query_cache") or "").lower()
    if rid and cache_q != "hit":
        a = _audit_find_by_request_id(con, rid)
        if not a:
            return Check(name, False, "audit missing for request_id", {"rid": rid, "cache": cache_q}), data
        if a.get("action") != "nl2sql.query":
            return Check(name, False, f"audit action mismatch: {a.get('action')}", {"rid": rid, "audit": a}), data

    return Check(name, True, f"status=200 rows={len(rows)}", {"rid": rid}), data


def _check_agent_chat(
    base_url: str,
    con: sqlite3.Connection,
    actor: str,
    message: str,
    *,
    name: str,
    thread_id: str | None = None,
    debug: bool = True,
    tag_unique: bool = True,
    results_jsonl: Path,
) -> tuple[Check, dict, str]:
    msg = message
    if tag_unique:
        msg = f"{msg} [{uuid.uuid4().hex[:8]}-D]"
    payload: dict[str, Any] = {"message": msg, "actor": actor, "debug": debug}
    if thread_id:
        payload["thread_id"] = thread_id

    code, rid, data = _http_json("POST", f"{base_url}/agent/chat", json_body=payload)
    _append_jsonl(results_jsonl, {"case": name, "endpoint": "/agent/chat", "payload": payload, "status": code, "rid": rid, "resp": data})

    if code != 200 or not isinstance(data, dict):
        return Check(name, False, f"status={code}", {"rid": rid}), {}, thread_id or ""

    new_thread_id = str(data.get("thread_id") or thread_id or "")
    body_rid = data.get("request_id")
    if rid and body_rid and rid != body_rid:
        return Check(name, False, f"request_id mismatch header={rid} body={body_rid}", {"rid": rid}), data, new_thread_id

    # If debug and NL2SQL executed, expect audit unless cache-hit.
    last_ok = data.get("last_nl2sql_ok")
    last = data.get("last_nl2sql") or {}
    cache_q = (((last.get("cache") or {}).get("query_cache") or "") if isinstance(last, dict) else "").lower()
    if rid and last_ok is True and cache_q != "hit":
        a = _audit_find_by_request_id(con, rid)
        if not a:
            return Check(name, False, "audit missing for request_id", {"rid": rid, "cache": cache_q}), data, new_thread_id

    return Check(name, True, f"status=200 thread_id={new_thread_id}", {"rid": rid, "thread_id": new_thread_id}), data, new_thread_id


def _run_toy_suite(base_url: str, con: sqlite3.Connection, suite_path: Path, actor: str, results_jsonl: Path) -> list[Check]:
    cases = _load_suite(suite_path)
    checks: list[Check] = []
    for c in cases:
        typ = c.get("type")
        name = c.get("name") or typ
        if typ == "health":
            checks.append(_check_health(base_url, results_jsonl))
            continue
        if typ == "chat_query":
            chk, _ = _check_chat_query(
                base_url,
                con,
                actor,
                str(c.get("query") or ""),
                name=name,
                min_rows=int(c.get("min_rows", 1)),
                row_must_have=list(c.get("row_must_have") or []),
                tag_unique=True,
                results_jsonl=results_jsonl,
            )
            checks.append(chk)
            continue
        if typ == "agent_chat":
            chk, data, _tid = _check_agent_chat(
                base_url,
                con,
                actor,
                str(c.get("message") or ""),
                name=name,
                thread_id=None,
                debug=True,
                tag_unique=True,
                results_jsonl=results_jsonl,
            )
            # Additional expectations
            exp = c.get("expect") or {}
            if isinstance(exp, dict) and exp.get("last_nl2sql_ok") is True:
                if data.get("last_nl2sql_ok") is not True:
                    chk = Check(name, False, f"expected last_nl2sql_ok=true got {data.get('last_nl2sql_ok')}", chk.meta)
            checks.append(chk)
            continue
        if typ == "agent_script":
            steps = c.get("steps") or []
            thread_id: str | None = None
            for si, step in enumerate(steps):
                sname = f"{name}#{si+1}"
                chk, data, thread_id = _check_agent_chat(
                    base_url,
                    con,
                    actor,
                    str(step.get("message") or ""),
                    name=sname,
                    thread_id=thread_id,
                    debug=True,
                    tag_unique=(si == 0),  # tag only first step by default
                    results_jsonl=results_jsonl,
                )
                exp = step.get("expect") or {}
                if isinstance(exp, dict):
                    if "pending_reason" in exp:
                        pr = ((data.get("pending") or {}) if isinstance(data.get("pending"), dict) else {})
                        if pr.get("reason") != exp["pending_reason"]:
                            chk = Check(sname, False, f"expected pending.reason={exp['pending_reason']} got {pr.get('reason')}", chk.meta)
                    if exp.get("pending_is_null") is True:
                        if data.get("pending") is not None:
                            chk = Check(sname, False, "expected pending=None", chk.meta)
                    if exp.get("last_nl2sql_ok") is True:
                        if data.get("last_nl2sql_ok") is not True:
                            chk = Check(sname, False, f"expected last_nl2sql_ok=true got {data.get('last_nl2sql_ok')}", chk.meta)
                    if "current_query_contains" in exp:
                        cq = str(data.get("current_query") or "")
                        if exp["current_query_contains"] not in cq:
                            chk = Check(sname, False, f"expected current_query contains '{exp['current_query_contains']}' got '{cq}'", chk.meta)
                checks.append(chk)
            continue

        checks.append(Check(name, False, f"unknown case type: {typ}"))
    return checks


def _run_scale_suite(base_url: str, con: sqlite3.Connection, suite_path: Path, actor: str, results_jsonl: Path) -> tuple[list[Check], dict]:
    cases = _load_suite(suite_path)
    checks: list[Check] = []
    lat_chat: list[float] = []
    lat_agent: list[float] = []
    cache_hits_observed = 0

    for c in cases:
        typ = c.get("type")
        name = c.get("name") or typ

        if typ == "health":
            checks.append(_check_health(base_url, results_jsonl))
            continue

        if typ == "db_stats":
            stats = _db_stats(con)
            ok = True
            for k, minv in [("assets", c.get("min_assets")), ("events", c.get("min_events")), ("metrics", c.get("min_metrics"))]:
                if minv is None:
                    continue
                if stats.get(k, 0) < int(minv):
                    ok = False
            checks.append(Check(name, ok, f"stats={stats}", {"stats": stats}))
            continue

        if typ == "chat_query":
            chk, data = _check_chat_query(
                base_url,
                con,
                actor,
                str(c.get("query") or ""),
                name=name,
                min_rows=int(c.get("min_rows", 1)),
                row_must_have=None,
                tag_unique=True,  # avoid query-cache for warmups
                results_jsonl=results_jsonl,
            )
            checks.append(chk)
            # latency
            if isinstance(data, dict):
                t = (data.get("timings_ms") or {}).get("total")
                if isinstance(t, (int, float)):
                    lat_chat.append(float(t))
                else:
                    lat_chat.append(float(data.get("_client_elapsed_ms") or 0))
            continue

        if typ == "chat_query_repeat":
            q = str(c.get("query") or "")
            repeat = int(c.get("repeat", 3))
            saw_hit = False
            for i in range(repeat):
                chk, data = _check_chat_query(
                    base_url,
                    con,
                    actor,
                    q,
                    name=f"{name}#{i+1}",
                    min_rows=1,
                    row_must_have=None,
                    tag_unique=False,  # allow cache hits
                    results_jsonl=results_jsonl,
                )
                checks.append(chk)
                cache_q = ((data.get("cache") or {}).get("query_cache") or "").lower() if isinstance(data, dict) else ""
                if cache_q == "hit":
                    saw_hit = True
                    cache_hits_observed += 1
                # latency
                if isinstance(data, dict):
                    t = (data.get("timings_ms") or {}).get("total")
                    if isinstance(t, (int, float)):
                        lat_chat.append(float(t))
                    else:
                        lat_chat.append(float(data.get("_client_elapsed_ms") or 0))
            expect_hit = bool(c.get("expect_cache_hit", True))
            if expect_hit and settings.enable_cache and not saw_hit:
                checks.append(Check(name, False, "expected at least one query_cache hit in repeats"))
            continue

        if typ == "asset_batch":
            kind = str(c.get("kind") or "events")
            count = int(c.get("count", 10))
            if kind == "downtime":
                assets = _sample_assets_with_downtime(con, count)
            else:
                assets = _sample_assets_any(con, count)

            ok_all = True
            for a in assets:
                if kind == "events":
                    q = f"{a} 이벤트"
                elif kind == "downtime":
                    q = f"{a} 다운타임"
                else:
                    q = f"{a} 이벤트"
                chk, data = _check_chat_query(
                    base_url,
                    con,
                    actor,
                    q,
                    name=f"{name}:{a}",
                    min_rows=1,
                    row_must_have=None,
                    tag_unique=False,
                    results_jsonl=results_jsonl,
                )
                checks.append(chk)
                ok_all = ok_all and chk.ok
                if isinstance(data, dict):
                    t = (data.get("timings_ms") or {}).get("total")
                    if isinstance(t, (int, float)):
                        lat_chat.append(float(t))
                    else:
                        lat_chat.append(float(data.get("_client_elapsed_ms") or 0))
            checks.append(Check(name, ok_all, f"assets_tested={len(assets)} kind={kind}"))
            continue

        checks.append(Check(name, False, f"unknown case type: {typ}"))

    perf = {
        "latency_chat_p50_ms": float(median(lat_chat)) if lat_chat else 0.0,
        "latency_chat_p95_ms": float(_p95(lat_chat)) if lat_chat else 0.0,
        "cache_hits_observed": cache_hits_observed,
    }
    return checks, perf


def _render_report(summary: dict) -> str:
    lines = []
    lines.append(f"# Milestone D report")
    lines.append("")
    lines.append(f"- scenario: `{summary.get('scenario')}`")
    lines.append(f"- base_url: `{summary.get('base_url')}`")
    lines.append(f"- sqlite_db: `{summary.get('sqlite_db')}`")
    lines.append(f"- enable_cache: `{summary.get('enable_cache')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- passed: {summary.get('passed')}")
    lines.append(f"- failed: {summary.get('failed')}")
    lines.append(f"- duration_s: {summary.get('duration_s'):.2f}")
    lines.append("")
    if summary.get("db_stats"):
        lines.append("## DB stats")
        lines.append("```")
        lines.append(json.dumps(summary["db_stats"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    if summary.get("metrics_diff"):
        lines.append("## Metrics diff")
        lines.append("```")
        lines.append(json.dumps(summary["metrics_diff"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    if summary.get("perf"):
        lines.append("## Performance")
        lines.append("```")
        lines.append(json.dumps(summary["perf"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.append("## Checks")
    for c in summary.get("checks", []):
        status = "PASS" if c["ok"] else "FAIL"
        lines.append(f"- [{status}] {c['name']}: {c['detail']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["toy", "scale"], default="toy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8750)
    ap.add_argument("--actor", default="me")
    ap.add_argument("--suite", default=None, help="Override suite JSONL path")
    ap.add_argument("--out-root", default=str(REPO_ROOT / "runs"))
    ap.add_argument("--sqlite-db", default=None, help="Explicit sqlite db path for audit/db_stats checks")
    args = ap.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    out_root = Path(args.out_root).resolve()
    run_dir = _make_run_dir(out_root)
    results_jsonl = run_dir / "results.jsonl"

    suite_default = REPO_ROOT / "eval" / "suites" / ("toy_smoke.jsonl" if args.scenario == "toy" else "scale_augmented.jsonl")
    suite_path = Path(args.suite).resolve() if args.suite else suite_default

    # Resolve sqlite path: prefer explicit, else from settings
    sqlite_db = Path(args.sqlite_db).resolve() if args.sqlite_db else _resolve_sqlite_path(settings.sqlalchemy_database_url)

    con = _db_connect(sqlite_db)

    metrics_before = _metrics_snapshot(base_url)
    audit_before = _audit_total(con)
    t_start = time.time()

    checks: list[Check] = []
    perf: dict[str, Any] | None = None

    if args.scenario == "toy":
        checks = _run_toy_suite(base_url, con, suite_path, args.actor, results_jsonl)
    else:
        checks, perf = _run_scale_suite(base_url, con, suite_path, args.actor, results_jsonl)

    duration_s = time.time() - t_start
    metrics_after = _metrics_snapshot(base_url)
    audit_after = _audit_total(con)

    metrics_diff = {
        "http_requests_total": _counter(metrics_after, "http_requests_total") - _counter(metrics_before, "http_requests_total"),
        "nl2sql_requests_total": _counter(metrics_after, "nl2sql_requests_total") - _counter(metrics_before, "nl2sql_requests_total"),
        "nl2sql_cache_hit_total": _counter(metrics_after, "nl2sql_cache_hit_total") - _counter(metrics_before, "nl2sql_cache_hit_total"),
    }

    passed = sum(1 for c in checks if c.ok)
    failed = sum(1 for c in checks if not c.ok)

    summary = {
        "scenario": args.scenario,
        "base_url": base_url,
        "suite": str(suite_path),
        "sqlite_db": str(sqlite_db),
        "enable_cache": bool(settings.enable_cache),
        "duration_s": duration_s,
        "passed": passed,
        "failed": failed,
        "audit_diff": audit_after - audit_before,
        "db_stats": _db_stats(con),
        "metrics_before": {
            "http_requests_total": _counter(metrics_before, "http_requests_total"),
            "nl2sql_requests_total": _counter(metrics_before, "nl2sql_requests_total"),
            "nl2sql_cache_hit_total": _counter(metrics_before, "nl2sql_cache_hit_total"),
        },
        "metrics_after": {
            "http_requests_total": _counter(metrics_after, "http_requests_total"),
            "nl2sql_requests_total": _counter(metrics_after, "nl2sql_requests_total"),
            "nl2sql_cache_hit_total": _counter(metrics_after, "nl2sql_cache_hit_total"),
        },
        "metrics_diff": metrics_diff,
        "perf": perf or {},
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, "meta": c.meta or {}} for c in checks],
    }

    _write_json(run_dir / "summary.json", summary)
    (run_dir / "report.md").write_text(_render_report(summary), encoding="utf-8")

    print(f"[eval_d] run_dir: {run_dir}")
    print(f"[eval_d] passed={passed} failed={failed} duration_s={duration_s:.2f}")
    print(f"[eval_d] metrics_diff: {metrics_diff}")
    print(f"[eval_d] audit_diff: {audit_after - audit_before}")
    if perf:
        print(f"[eval_d] perf: {perf}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
