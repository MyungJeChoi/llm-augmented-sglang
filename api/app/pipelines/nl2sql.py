from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.llm.tasks import llm_make_nl2sql_plan, llm_prepare_query
from app.llm.schemas import NL2SQLPlan, TimeRange
from app.ops.metrics import metrics
from app.services.audit import AuditLogger
from app.services.cache import InMemoryTTLCache
from app.services.kg_client import KGClient
from app.services.meta_store import MetaStore
from app.services.sql_client import SQLClient

SAFE_SQL_RE = re.compile(r"^\s*select\b", re.IGNORECASE)
RANK_TOKEN_RE = re.compile(r"(?i)^(?:top\s*\d+|top\d+)$")
ASSET_CODE_RE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*[-_]?\d{2,}\b")


def is_rank_token(tok: str) -> bool:
    return bool(RANK_TOKEN_RE.match(tok))

# Process-wide cache (single worker assumption)
_CACHE = InMemoryTTLCache(max_items=settings.cache_max_items)


def normalize_query(q: str) -> str:
    return " ".join(q.strip().split())


def extract_candidate_terms(q: str) -> list[str]:
    # MVP: heuristic tokenization (Korean-friendly: keep 2+ length tokens)
    tokens = re.findall(r"[0-9A-Za-z가-힣_]+", q)
    return [t for t in tokens if len(t) >= 2 and not is_rank_token(t)][:10]


def rewrite_query_with_kg(q: str, kg: KGClient, kg_version: str, ttl_s: int) -> tuple[str, dict]:
    """Rewrite tokens using KG synonyms -> canonical.

    Returns (rewritten_query, rewrite_debug)
    """

    tokens = extract_candidate_terms(q)
    rewritten = q
    
    # ------------------
    # TODO: canon_hits, canon_misses 변수명 바꾸는게 직관적일듯
    # canon_hits 은 canonical 단어를 찾은 수처럼 느껴짐
    # ------------------
    canon_hits = 0
    canon_misses = 0

    for tok in tokens:
        ck = f"canon:{kg_version}:{tok}"
        can = _CACHE.get(ck) if settings.enable_cache else None
        if can is not None:
            canon_hits += 1
            can_text = can.get("canonical", tok)
        else:
            canon_misses += 1
            can_text = kg.canonicalize(tok)
            if settings.enable_cache:
                _CACHE.set(ck, {"canonical": can_text}, ttl_seconds=ttl_s)

        if can_text != tok:
            rewritten = rewritten.replace(tok, can_text)

    return rewritten, {"tokens": tokens, "canon_hits": canon_hits, "canon_misses": canon_misses}


def build_kg_context(q: str, kg: KGClient, kg_version: str, ttl_s: int) -> tuple[dict, dict]:
    terms = extract_candidate_terms(q)
    key = f"kgctx:{kg_version}:{'|'.join(terms)}"

    cached = _CACHE.get(key) if settings.enable_cache else None
    if cached is not None:
        return cached, {"cache": "hit", "terms": terms}

    mappings = kg.map_terms_to_columns(terms)
    ctx = {"terms": terms, "mappings": mappings}

    if settings.enable_cache:
        _CACHE.set(key, ctx, ttl_seconds=ttl_s)

    return ctx, {"cache": "miss", "terms": terms}


def generate_sql_mock(q: str, context: dict) -> str:
    """Template-based SQL generator (SQLite-friendly).

    Keep intentionally tiny so the scaffold is runnable without an LLM.
    You will later replace this with an LLM-backed SQL generator.
    """

    # --- Asset-specific patterns (e.g., ETCH-01 이벤트) ---
    asset = None
    for tok in ASSET_CODE_RE.findall(q.upper()):
        if is_rank_token(tok):
            continue
        asset = tok
        break

    if asset and ("이벤트" in q or "event" in q.lower() or "로그" in q):
        return f"""
        SELECT e.event_id, a.asset_name, e.event_type, e.start_ts, e.end_ts, e.severity
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        WHERE a.asset_name = '{asset}'
        ORDER BY e.start_ts DESC
        LIMIT 50;
        """

    if asset and ("다운타임" in q or "정지시간" in q or "비가동" in q):
        return f"""
        SELECT a.asset_name,
               ROUND(SUM((julianday(e.end_ts) - julianday(e.start_ts)) * 24.0), 3) AS downtime_hours
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        WHERE e.event_type = 'downtime'
          AND e.end_ts IS NOT NULL
          AND a.asset_name = '{asset}'
        GROUP BY a.asset_name
        ORDER BY downtime_hours DESC
        LIMIT 1;
        """

    if asset and ("온도" in q or "temperature" in q.lower()):
        return f"""
        SELECT a.asset_name, ROUND(AVG(m.value), 3) AS avg_temp
        FROM metrics m
        JOIN assets a ON a.asset_id = m.asset_id
        WHERE m.metric_name = 'temperature'
          AND a.asset_name = '{asset}'
        GROUP BY a.asset_name
        LIMIT 1;
        """

    # --- Global patterns ---
    if "다운타임" in q or "정지시간" in q or "비가동" in q:
        # downtime hours per asset (events table)
        # SQLite: (julianday(end) - julianday(start)) * 24
        return """
        SELECT a.asset_name,
               ROUND(SUM((julianday(e.end_ts) - julianday(e.start_ts)) * 24.0), 3) AS downtime_hours
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        WHERE e.event_type = 'downtime' AND e.end_ts IS NOT NULL
        GROUP BY a.asset_name
        ORDER BY downtime_hours DESC
        LIMIT 10;
        """

    if "온도" in q:
        return """
        SELECT a.asset_name, ROUND(AVG(m.value), 3) AS avg_temp
        FROM metrics m
        JOIN assets a ON a.asset_id = m.asset_id
        WHERE m.metric_name = 'temperature'
        GROUP BY a.asset_name
        ORDER BY avg_temp DESC
        LIMIT 10;
        """

    # fallback: show recent events
    return """
    SELECT e.event_id, a.asset_name, e.event_type, e.start_ts, e.end_ts, e.severity
    FROM events e
    JOIN assets a ON a.asset_id = e.asset_id
    ORDER BY e.start_ts DESC
    LIMIT 20;
    """


# ---------------------------
# LLM-backed SQL planning (Milestone D)
# ---------------------------

_SCHEMA_SUMMARY_CACHE: str | None = None


def _schema_summary(sqlc: SQLClient) -> str:
    """Build a compact schema summary string for prompting.

    Cached per process because schema is stable for the scaffold.
    """
    global _SCHEMA_SUMMARY_CACHE
    if _SCHEMA_SUMMARY_CACHE:
        return _SCHEMA_SUMMARY_CACHE

    tables = sqlc.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    table_names = [t["name"] for t in tables if t.get("name") and not str(t["name"]).startswith("sqlite_")]

    lines: list[str] = []
    for name in table_names:
        cols = sqlc.query(f"PRAGMA table_info('{name}');")
        col_names = [c.get("name") for c in cols if c.get("name")]
        lines.append(f"- {name}({', '.join(map(str, col_names))})")

    _SCHEMA_SUMMARY_CACHE = "\n".join(lines) if lines else "(no tables)"
    return _SCHEMA_SUMMARY_CACHE


def _clamp_int(v: int | None, *, default: int, lo: int, hi: int) -> int:
    try:
        x = int(v) if v is not None else int(default)
    except Exception:
        x = int(default)
    return max(lo, min(hi, x))


def _format_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _resolve_time_range(tr: TimeRange | None, *, now: datetime) -> tuple[str, str] | None:
    if tr is None or tr.kind == "none":
        return None

    if tr.kind == "relative_days":
        days = _clamp_int(tr.last_days, default=7, lo=1, hi=3650)
        start = now - timedelta(days=days)
        return _format_ts(start), _format_ts(now)

    if tr.kind == "absolute":
        # Allow date-only strings; normalize to full timestamp bounds.
        start = (tr.start or "").strip()
        end = (tr.end or "").strip()
        if not start and not end:
            return None
        if start and len(start) == 10:
            start = start + " 00:00:00"
        if end and len(end) == 10:
            # inclusive end-of-day -> use next day 00:00:00 as exclusive bound
            try:
                dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
                end = _format_ts(dt)
            except Exception:
                end = end + " 23:59:59"
        # If only one side exists, keep the other as now.
        if start and not end:
            end = _format_ts(now)
        if end and not start:
            # best-effort: last 7 days ending at end
            try:
                end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
            except Exception:
                end_dt = now
            start = _format_ts(end_dt - timedelta(days=7))
        return start, end

    return None


def _compile_sql_from_plan(plan: NL2SQLPlan, *, now: datetime) -> tuple[str, dict, dict]:
    """Compile a safe parameterized SQL from an NL2SQLPlan.

    Returns (sql, params, compile_debug)
    """
    tid = plan.template_id
    params: dict[str, object] = {}
    dbg: dict[str, object] = {"template_id": tid}

    # Resolve time range (exclusive end bound)
    tr = _resolve_time_range(plan.time_range, now=now)
    if tr:
        dbg["time_range_resolved"] = {"start": tr[0], "end": tr[1]}

    if tid == "recent_events":
        limit = _clamp_int(plan.limit, default=20, lo=1, hi=200)
        sql = f"""
        SELECT e.event_id, a.asset_name, e.event_type, e.start_ts, e.end_ts, e.severity
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        ORDER BY e.start_ts DESC
        LIMIT {limit};
        """
        return sql, params, dbg

    if tid == "asset_events":
        asset = (plan.asset_name or "").strip()
        if not asset:
            raise ValueError("asset_events requires asset_name")
        limit = _clamp_int(plan.limit, default=50, lo=1, hi=200)
        params["asset_name"] = asset

        where = ["a.asset_name = :asset_name"]
        if tr:
            params["start_ts"] = tr[0]
            params["end_ts"] = tr[1]
            where.append("e.start_ts >= :start_ts AND e.start_ts < :end_ts")

        sql = f"""
        SELECT e.event_id, a.asset_name, e.event_type, e.start_ts, e.end_ts, e.severity
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        WHERE {' AND '.join(where)}
        ORDER BY e.start_ts DESC
        LIMIT {limit};
        """
        return sql, params, dbg

    if tid == "asset_downtime":
        asset = (plan.asset_name or "").strip()
        if not asset:
            raise ValueError("asset_downtime requires asset_name")
        params["asset_name"] = asset

        where = [
            "e.event_type = 'downtime'",
            "e.end_ts IS NOT NULL",
            "a.asset_name = :asset_name",
        ]
        if tr:
            params["start_ts"] = tr[0]
            params["end_ts"] = tr[1]
            where.append("e.start_ts >= :start_ts AND e.start_ts < :end_ts")

        sql = f"""
        SELECT a.asset_name,
               ROUND(SUM((julianday(e.end_ts) - julianday(e.start_ts)) * 24.0), 3) AS downtime_hours
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        WHERE {' AND '.join(where)}
        GROUP BY a.asset_name
        ORDER BY downtime_hours DESC
        LIMIT 1;
        """
        return sql, params, dbg

    if tid == "asset_avg_temperature":
        asset = (plan.asset_name or "").strip()
        if not asset:
            raise ValueError("asset_avg_temperature requires asset_name")
        params["asset_name"] = asset

        where = [
            "m.metric_name = 'temperature'",
            "a.asset_name = :asset_name",
        ]
        if tr:
            params["start_ts"] = tr[0]
            params["end_ts"] = tr[1]
            where.append("m.ts >= :start_ts AND m.ts < :end_ts")

        sql = f"""
        SELECT a.asset_name, ROUND(AVG(m.value), 3) AS avg_temp
        FROM metrics m
        JOIN assets a ON a.asset_id = m.asset_id
        WHERE {' AND '.join(where)}
        GROUP BY a.asset_name
        LIMIT 1;
        """
        return sql, params, dbg

    if tid == "topk_downtime_assets":
        top_k = _clamp_int(plan.top_k, default=10, lo=1, hi=100)
        where = [
            "e.event_type = 'downtime'",
            "e.end_ts IS NOT NULL",
        ]
        if tr:
            params["start_ts"] = tr[0]
            params["end_ts"] = tr[1]
            where.append("e.start_ts >= :start_ts AND e.start_ts < :end_ts")

        sql = f"""
        SELECT a.asset_name,
               ROUND(SUM((julianday(e.end_ts) - julianday(e.start_ts)) * 24.0), 3) AS downtime_hours
        FROM events e
        JOIN assets a ON a.asset_id = e.asset_id
        WHERE {' AND '.join(where)}
        GROUP BY a.asset_name
        ORDER BY downtime_hours DESC
        LIMIT {top_k};
        """
        return sql, params, dbg

    if tid == "topk_temperature_assets":
        top_k = _clamp_int(plan.top_k, default=10, lo=1, hi=100)
        where = ["m.metric_name = 'temperature'"]
        if tr:
            params["start_ts"] = tr[0]
            params["end_ts"] = tr[1]
            where.append("m.ts >= :start_ts AND m.ts < :end_ts")

        sql = f"""
        SELECT a.asset_name, ROUND(AVG(m.value), 3) AS avg_temp
        FROM metrics m
        JOIN assets a ON a.asset_id = m.asset_id
        WHERE {' AND '.join(where)}
        GROUP BY a.asset_name
        ORDER BY avg_temp DESC
        LIMIT {top_k};
        """
        return sql, params, dbg

    raise ValueError(f"unknown template_id: {tid}")


def generate_sql_llm(q: str, *, kg_context: dict, sqlc: SQLClient) -> tuple[str, dict, dict]:
    """LLM-backed planner -> template compiler.

    Returns (sql, params, sqlgen_debug).
    """
    schema = _schema_summary(sqlc)
    plan, dbg = llm_make_nl2sql_plan(q, schema_summary=schema, kg_context=kg_context)

    now = datetime.now(timezone.utc)
    sql, params, cdbg = _compile_sql_from_plan(plan, now=now)

    out_dbg = {
        "backend": "llm_plan+template",
        "plan": plan.model_dump(),
        "llm": dbg,
        "compile": cdbg,
    }
    return sql, params, out_dbg


def validate_sql(sql: str) -> None:
    if not SAFE_SQL_RE.match(sql):
        raise ValueError("Only SELECT statements are allowed.")
    forbidden = ["insert", "update", "delete", "drop", "alter"]
    if any(k in sql.lower() for k in forbidden):
        raise ValueError("Forbidden keyword in SQL.")


def run_nl2sql_pipeline(query: str, actor: str | None = None, request_id: str | None = None) -> dict:
    """End-to-end NL2SQL pipeline.
    
    - per-stage timings
    - request_id propagation
    - caching (KG canonicalization + KG context)
    - lightweight metrics counters
    """

    audit = AuditLogger(settings.sqlalchemy_database_url)
    meta = MetaStore(settings.sqlalchemy_database_url)
    kg_version = meta.get("kg_version", default="unknown") or "unknown"

    ttl_s = int(settings.cache_ttl_seconds)

    kg = KGClient(settings.neo4j_bolt_url, settings.neo4j_user, settings.neo4j_password)
    sqlc = SQLClient(settings.sqlalchemy_database_url)

    q0_raw = normalize_query(query)
    if not q0_raw:
        return {
            "ok": False,
            "request_id": request_id,
            "error": {"code": "EMPTY_QUERY", "message": "query is empty"},
        }

    # Optional LLM query preparation (normalize into a canonical short form)
    q0 = q0_raw
    prep_debug = None
    if settings.llm_enable_query_prep:
        try:
            # LLM으로 query 정규화
            q_prep, prep_debug = llm_prepare_query(q0_raw)
            q0 = normalize_query(q_prep) or q0_raw
        except Exception as e:
            prep_debug = {"error": str(e)}
            q0 = q0_raw

    # Optional query-level cache (fast path)
    backend_tag = "heuristic"
    if settings.llm_enable_query_prep or settings.llm_enable_sqlgen:
        backend_tag = f"llm(model={settings.llm_model},pv={settings.llm_prompt_version},prep={int(settings.llm_enable_query_prep)},sqlgen={int(settings.llm_enable_sqlgen)})"
    q_cache_key = f"nl2sql:{kg_version}:{backend_tag}:{q0}"
    if settings.enable_cache:
        cached = _CACHE.get(q_cache_key)
        if cached is not None:
            metrics.inc("nl2sql_cache_hit_total", 1)
            cached["cache"] = {"query_cache": "hit"}
            cached["request_id"] = request_id
            return cached

    metrics.inc("nl2sql_requests_total", 1)

    t_total0 = time.perf_counter()
    timings = {}

    try:
        # Rewrite
        t0 = time.perf_counter()
        q1, rewrite_debug = rewrite_query_with_kg(q0, kg, kg_version=kg_version, ttl_s=ttl_s)
        timings["rewrite"] = (time.perf_counter() - t0) * 1000.0

        # KG context
        t0 = time.perf_counter()
        ctx, ctx_debug = build_kg_context(q1, kg, kg_version=kg_version, ttl_s=ttl_s)
        timings["kg_context"] = (time.perf_counter() - t0) * 1000.0

        # SQL generation
        t0 = time.perf_counter()
        sql_params = None
        sqlgen_debug = {"backend": "heuristic_template"}
        if settings.llm_enable_sqlgen:
            sql, sql_params, sqlgen_debug = generate_sql_llm(q1, kg_context=ctx, sqlc=sqlc)
        else:
            sql = generate_sql_mock(q1, ctx)

        # sql 처리는 select만 가능
        validate_sql(sql)
        timings["sqlgen"] = (time.perf_counter() - t0) * 1000.0

        # Execution
        t0 = time.perf_counter()
        rows = sqlc.query(sql, params=sql_params)
        timings["execute"] = (time.perf_counter() - t0) * 1000.0

        total_ms = (time.perf_counter() - t_total0) * 1000.0
        timings["total"] = total_ms

        result = {
            "ok": True,
            "request_id": request_id,
            "kg_version": kg_version,
            "query_original": q0_raw,
            "query_prepared": q0,
            "query_prep_debug": prep_debug,
            "query_rewritten": q1,
            "rewrite_debug": rewrite_debug,
            "kg_context": ctx,
            "kg_context_debug": ctx_debug,
            "sql": sql.strip(),
            "sql_params": sql_params,
            "sqlgen_debug": sqlgen_debug,
            "rows": rows,
            "timings_ms": timings,
            "cache": {
                "query_cache": "miss",
                "backend_tag": backend_tag,
                "kgctx_cache": ctx_debug.get("cache"),
                "canon_hits": rewrite_debug.get("canon_hits"),
                "canon_misses": rewrite_debug.get("canon_misses"),
            },
        }

        # record metrics
        metrics.observe_nl2sql_latency(total_ms, stages={k: v for k, v in timings.items() if k in {"rewrite", "kg_context", "sqlgen", "execute"}})

        audit.log("nl2sql.query", result, actor=actor, request_id=request_id)

        if settings.enable_cache:
            _CACHE.set(q_cache_key, result, ttl_seconds=ttl_s)

        return result

    except Exception as e:
        metrics.inc("nl2sql_errors_total", 1)
        err = {
            "code": "NL2SQL_ERROR",
            "message": str(e),
            "type": e.__class__.__name__,
        }
        fail = {
            "ok": False,
            "request_id": request_id,
            "kg_version": kg_version,
            "query_original": q0_raw,
            "query_prepared": q0,
            "query_prep_debug": prep_debug,
            "error": err,
        }
        audit.log("nl2sql.error", fail, actor=actor, request_id=request_id)
        return fail

    finally:
        kg.close()
