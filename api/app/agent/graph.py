from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent.utils import (
    extract_asset_mentions,
    last_user_text,
    merge_clarification,
    needs_metric_clarification,
    parse_rank_request,
    pick_asset_from_last_result,
)
from app.core.config import settings
from app.pipelines.nl2sql import run_nl2sql_pipeline
from app.llm.tasks import llm_classify_intent, llm_route_nl2sql, llm_prepare_query
from app.services.sql_client import SQLClient


class AgentState(MessagesState, total=False):
    """Graph state.
    
    - messages: inherited from MessagesState (append-only w/ reducer)
    - pending: holds pending clarification info (if any)
    - current_query: what we will send into NL2SQL
    - intent: which tool/handler we picked
    - last_nl2sql: last NL2SQL result blob (for follow-ups)
    - next_action: used to route after nl2sql_router
    - request_id/actor: request context used by audit logging
    """

    pending: dict
    current_query: str
    intent: str
    intent_debug: dict
    last_nl2sql: dict
    next_action: str
    router_debug: dict
    request_id: str
    actor: str


# ---------------------------
# Node helpers
# ---------------------------

def _get_metadata(config: dict | None) -> dict:
    if not config:
        return {}
    md = config.get("metadata") or {}
    return md if isinstance(md, dict) else {}


# ---------------------------
# Nodes
# ---------------------------

def prepare(state: AgentState, config: dict | None = None) -> dict:
    """Prepare `current_query`.

    Handles:
    - pending clarification merges
    - follow-up turns like "1등 장비 이벤트 보여줘"
    - (optional) LLM query preparation / normalization
    """

    user_text = last_user_text(state)

    def _maybe_llm_prep(q: str) -> str:
        if not settings.llm_enable_query_prep:
            return q
        try:
            # llm_prepare_query : llm이 query 정규화시킴
            q2, _dbg = llm_prepare_query(q)
            return (q2 or q).strip()
        except Exception:
            return q

    # 1) If we previously asked for clarification, merge it.
    # pending: 이전에 추가 질문이 필요한 불완전한 쿼리가 있었는지 여부/내용
    # pending이 있으면 base_query와 사용자 응답을 병합해 최종 쿼리를 만들고,
    # 처리 완료 후 pending은 None으로 비워 대기 상태를 해제한다.
    
    # --------------------------------
    # TODO: clarification 상황에 대해 더 구체화할 필요가 있다.
    # (예) 현재는 기존 응답이 무조건 "장비 top10", 추가 응답이 "다운타임" 같이 조회 기준이 들어오는 걸로 가정함.
    # 실제로는 "다운타임 top10" 과 같은 입력이 들어왔을 때, 대처 가능한지?
    # --------------------------------
    
    pending = state.get("pending")
    if pending and isinstance(pending, dict) and pending.get("base_query"):
        merged = merge_clarification(str(pending.get("base_query")), user_text)
        merged = _maybe_llm_prep(merged)
        return {"current_query": merged, "pending": None}

    # 2) Follow-up rank referencing previous NL2SQL results
    rank = parse_rank_request(user_text)
    mentions = extract_asset_mentions(user_text)
    
    # top10류 질의가 들어온 후, 추가 질의일 때 작동 (예. 3등 보여줘, 1등 보여줘)
    if not mentions and rank:
        asset = pick_asset_from_last_result(state.get("last_nl2sql"), rank_1based=rank)
        if asset:
            if any(k in user_text for k in ["이벤트", "event", "로그"]):
                q = f"{asset} 이벤트"
            elif any(k in user_text for k in ["다운타임", "정지시간", "비가동"]):
                q = f"{asset} 다운타임"
            else:
                q = f"{asset} 이벤트"
            q = _maybe_llm_prep(q)
            return {"current_query": q}

    # Default: the raw user text is the query
    q = _maybe_llm_prep(user_text)
    return {"current_query": q}


def classify_intent(state: AgentState, config: dict | None = None) -> dict:
    """Intent router.

    - Default: heuristic (keywords)
    - Optional: LLM router (structured choice) when enabled via settings
    """

    q = (state.get("current_query") or last_user_text(state) or "").strip()
    ql = q.lower()

    # LLM router (opt-in)
    if settings.llm_enable_intent_router:
        try:
            intent, dbg = llm_classify_intent(q)
            return {"intent": intent, "intent_debug": dbg}
        except Exception:
            # Fall back to heuristic (safe default)
            pass

    # Heuristic fallback
    if any(k in ql for k in ["show sql", "sql 보여", "쿼리 보여", "query 보여", "last sql"]):
        return {"intent": "show_sql"}

    # NOTE: Do NOT treat generic "sql" token as show_sql (often means "SQL로 작성해줘").
    if any(k in ql for k in ["스키마", "schema", "테이블", "컬럼", "column"]):
        return {"intent": "describe_schema"}

    if any(k in ql for k in ["설명", "왜", "어떻게", "rewrite", "리라이트", "canonical"]):
        return {"intent": "explain_last"}

    return {"intent": "nl2sql"}


def nl2sql_router(state: AgentState, config: dict | None = None) -> dict:
    """Decide whether we can run NL2SQL immediately or need clarification.

    Strategy:
    - Hard-guard: keep the metric-clarification heuristic (high precision)
    - Optional: LLM router for additional missing-slot cases (asset/time_range/etc.)
    """

    q = (state.get("current_query") or "").strip()

    # Hard guard: ranking queries MUST specify a metric (downtime/temp/etc.)
    if needs_metric_clarification(q):
        question = (
            "TOP/상위 질의는 기준(지표)이 필요합니다. "
            "예: `다운타임 많은 장비 top10`, `온도 높은 장비 top10`. "
            "어떤 지표로 볼까요?"
        )
        return {
            "pending": {"reason": "need_metric", "base_query": q},
            "next_action": "clarify",
            "messages": [{"role": "ai", "content": question}],
        }

    # LLM router (opt-in)
    if settings.llm_enable_nl2sql_router:
        try:
            # decision: action, clarification
            # clarification: missing, question
            decision, dbg = llm_route_nl2sql(q)
            if decision.action == "clarify" and decision.clarification:
                return {
                    "pending": {"reason": "llm_clarify", "base_query": q, "missing": decision.clarification.missing},
                    "next_action": "clarify",
                    "router_debug": dbg,
                    "messages": [{"role": "ai", "content": decision.clarification.question}],
                }
            return {"next_action": "run", "router_debug": dbg}
        except Exception:
            # fallback: run
            pass

    return {"next_action": "run"}


def run_nl2sql(state: AgentState, config: dict | None = None) -> dict:
    md = _get_metadata(config)
    rid = md.get("request_id") or state.get("request_id")
    actor = md.get("actor") or state.get("actor")

    q = (state.get("current_query") or "").strip()

    result = run_nl2sql_pipeline(q, actor=actor, request_id=rid)

    # Build a compact assistant answer.
    if not result.get("ok"):
        msg = f"질의 처리 중 오류가 발생했습니다: {result.get('error', {}).get('message', 'unknown')}"
        return {"last_nl2sql": result, "messages": [{"role": "ai", "content": msg}]}

    rows = result.get("rows") or []
    if not rows:
        msg = "결과가 없습니다. 다른 조건으로 질의해보세요."
        return {"last_nl2sql": result, "messages": [{"role": "ai", "content": msg}]}

    # Detect common patterns
    if "downtime_hours" in rows[0]:
        lines = ["다운타임 TOP 결과 (hours):"]
        for i, r in enumerate(rows[:10], start=1):
            lines.append(f"{i}. {r.get('asset_name')}: {r.get('downtime_hours')}h")
        lines.append("\n후속 예: `1등 장비 이벤트 보여줘`, `ETCH-01 다운타임 보여줘`")
        msg = "\n".join(lines)
        return {"last_nl2sql": result, "messages": [{"role": "ai", "content": msg}]}

    if "avg_temp" in rows[0]:
        lines = ["온도 TOP 결과 (avg):"]
        for i, r in enumerate(rows[:10], start=1):
            lines.append(f"{i}. {r.get('asset_name')}: {r.get('avg_temp')}")
        lines.append("\n후속 예: `1등 장비 이벤트 보여줘`")
        msg = "\n".join(lines)
        return {"last_nl2sql": result, "messages": [{"role": "ai", "content": msg}]}

    # Generic fallback
    msg = f"{len(rows)}건의 결과를 조회했습니다. (예: 첫 행: {rows[0]})"
    return {"last_nl2sql": result, "messages": [{"role": "ai", "content": msg}]}


def show_sql(state: AgentState, config: dict | None = None) -> dict:
    last = state.get("last_nl2sql")
    if not last or not isinstance(last, dict):
        return {"messages": [{"role": "ai", "content": "아직 실행된 NL2SQL 결과가 없습니다."}]}

    sql = (last.get("sql") or "").strip()
    if not sql:
        return {"messages": [{"role": "ai", "content": "마지막 결과에 SQL이 없습니다."}]}

    msg = "마지막으로 실행된 SQL은 다음과 같습니다:\n\n" + sql
    return {"messages": [{"role": "ai", "content": msg}]}


def describe_schema(state: AgentState, config: dict | None = None) -> dict:
    """Return a human-readable summary of the SQLite schema."""

    sqlc = SQLClient(settings.sqlalchemy_database_url)

    tables = sqlc.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    table_names = [t["name"] for t in tables if t.get("name") and not str(t["name"]).startswith("sqlite_")]

    lines: list[str] = []
    if not table_names:
        return {"messages": [{"role": "ai", "content": "테이블을 찾지 못했습니다."}]}

    lines.append("현재 SQLite 스키마 요약:")
    for name in table_names:
        cols = sqlc.query(f"PRAGMA table_info('{name}');")
        col_names = [c.get("name") for c in cols if c.get("name")]
        lines.append(f"- {name}({', '.join(map(str, col_names))})")

    lines.append("\n후속 예: `SQL 보여줘`, `설명해줘`, `다운타임 많은 장비 top10`")
    return {"messages": [{"role": "ai", "content": "\n".join(lines)}]}


def explain_last(state: AgentState, config: dict | None = None) -> dict:
    last = state.get("last_nl2sql")
    if not last or not isinstance(last, dict):
        return {"messages": [{"role": "ai", "content": "설명할 NL2SQL 결과가 없습니다. 먼저 질의를 실행해보세요."}]}

    if not last.get("ok"):
        msg = f"마지막 NL2SQL은 실패했습니다. 에러: {last.get('error', {}).get('message', 'unknown')}"
        return {"messages": [{"role": "ai", "content": msg}]}

    parts = []
    parts.append(f"- query_original: {last.get('query_original')}")
    parts.append(f"- query_rewritten: {last.get('query_rewritten')}")
    parts.append(f"- kg_version: {last.get('kg_version')}")

    cache = last.get("cache") or {}
    parts.append(
        "- cache: "
        f"query={cache.get('query_cache')}, kgctx={cache.get('kgctx_cache')}, "
        f"canon_hits={cache.get('canon_hits')}, canon_misses={cache.get('canon_misses')}"
    )

    timings = last.get("timings_ms") or {}
    parts.append(
        "- timings_ms: "
        f"rewrite={timings.get('rewrite'):.1f}, kg_context={timings.get('kg_context'):.1f}, "
        f"sqlgen={timings.get('sqlgen'):.1f}, execute={timings.get('execute'):.1f}, total={timings.get('total'):.1f}"
        if all(k in timings for k in ["rewrite", "kg_context", "sqlgen", "execute", "total"]) else "- timings_ms: (missing)"
    )

    msg = "마지막 NL2SQL 실행 과정을 요약하면:\n" + "\n".join(parts)
    return {"messages": [{"role": "ai", "content": msg}]}


# ---------------------------
# Routing
# ---------------------------

def _route_intent(state: AgentState) -> str:
    return (state.get("intent") or "nl2sql").strip()


def _route_nl2sql(state: AgentState) -> str:
    return (state.get("next_action") or "run").strip()


# ---------------------------
# Graph factory
# ---------------------------

def build_agent_graph(checkpointer: Any | None = None):
    builder = StateGraph(AgentState)

    builder.add_node("prepare", prepare)
    builder.add_node("classify", classify_intent)

    builder.add_node("show_sql", show_sql)
    builder.add_node("describe_schema", describe_schema)
    builder.add_node("explain_last", explain_last)

    builder.add_node("nl2sql_router", nl2sql_router)
    builder.add_node("run_nl2sql", run_nl2sql)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "classify")

    builder.add_conditional_edges(
        "classify",
        _route_intent,
        {
            "show_sql": "show_sql",
            "describe_schema": "describe_schema",
            "explain_last": "explain_last",
            "nl2sql": "nl2sql_router",
        },
    )

    builder.add_conditional_edges(
        "nl2sql_router",
        _route_nl2sql,
        {
            "clarify": END,
            "run": "run_nl2sql",
        },
    )

    builder.add_edge("run_nl2sql", END)
    builder.add_edge("show_sql", END)
    builder.add_edge("describe_schema", END)
    builder.add_edge("explain_last", END)

    return builder.compile(checkpointer=checkpointer)
