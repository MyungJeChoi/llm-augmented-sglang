from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.llm.client import OpenAICompatClient
from app.llm.prompts import intent_messages, nl2sql_plan_messages, nl2sql_router_messages, query_prep_messages
from app.llm.schemas import IntentLabel, NL2SQLPlan, NL2SQLRouterDecision, QueryPrep


_CLIENT: OpenAICompatClient | None = None


def get_llm_client() -> OpenAICompatClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAICompatClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            backend=getattr(settings, 'llm_backend', 'sglang'),
            timeout_s=settings.llm_timeout_s,
            disable_thinking=settings.llm_disable_thinking,
        )
    return _CLIENT


def llm_classify_intent(query: str) -> tuple[IntentLabel, dict[str, Any]]:
    client = get_llm_client()
    msgs = intent_messages(query)

    res = client.choose_one(
        msgs,
        choices=["show_sql", "describe_schema", "explain_last", "nl2sql"],
        temperature=settings.llm_temperature,
        max_tokens=16,
    )

    intent = (res.content or "").strip().strip('"').strip()
    if intent not in {"show_sql", "describe_schema", "explain_last", "nl2sql"}:
        raise ValueError(f"invalid intent label from LLM: {intent}")

    return intent, {"latency_ms": res.latency_ms, "raw": res.raw}


def llm_route_nl2sql(query: str) -> tuple[NL2SQLRouterDecision, dict[str, Any]]:
    """system, user query 합친 내용을 llm에 전달하여 라우팅 결과를 제공받음"""
    client = get_llm_client()
    msgs = nl2sql_router_messages(query)

    schema = NL2SQLRouterDecision.model_json_schema()
    # for generate_json(...),
    # If using SGLang, return (LLM message text, ChatResult), 
    # where ChatResult(content=str(content), raw=raw, latency_ms=float(dt))
    data, res = client.generate_json(
        msgs,
        json_schema=schema,
        schema_name=f"nl2sql-router-{settings.llm_prompt_version}",
        temperature=settings.llm_temperature,
        max_tokens=min(int(settings.llm_max_tokens), 256),
    )

    try:
        # model_validate : Pydantic v2 BaseModel
        # 해당 모델 schema 형태와 맞는지 파악하고, 맞으면 그 객체로 변환시킴
        decision = NL2SQLRouterDecision.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM router output validation failed: {e}") from e

    return decision, {"latency_ms": res.latency_ms, "raw": res.raw, "parsed": data}


def llm_make_nl2sql_plan(query: str, *, schema_summary: str, kg_context: dict | None = None) -> tuple[NL2SQLPlan, dict[str, Any]]:
    client = get_llm_client()
    msgs = nl2sql_plan_messages(query, schema_summary=schema_summary, kg_context=kg_context)

    schema = NL2SQLPlan.model_json_schema()
    data, res = client.generate_json(
        msgs,
        json_schema=schema,
        schema_name=f"nl2sql-plan-{settings.llm_prompt_version}",
        temperature=settings.llm_temperature,
        max_tokens=int(settings.llm_max_tokens),
    )

    try:
        plan = NL2SQLPlan.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM plan output validation failed: {e}") from e

    return plan, {"latency_ms": res.latency_ms, "raw": res.raw, "parsed": data}


def llm_prepare_query(query: str) -> tuple[str, dict[str, Any]]:
    """Normalize user query into a canonical short form (optional stage)."""
    client = get_llm_client()
    msgs = query_prep_messages(query)

    schema = QueryPrep.model_json_schema()
    data, res = client.generate_json(
        msgs,
        json_schema=schema,
        schema_name=f"query-prep-{settings.llm_prompt_version}",
        temperature=settings.llm_temperature,
        max_tokens=128,
    )

    try:
        qp = QueryPrep.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM query prep output validation failed: {e}") from e

    return qp.normalized_query.strip(), {"latency_ms": res.latency_ms, "raw": res.raw, "parsed": data}
