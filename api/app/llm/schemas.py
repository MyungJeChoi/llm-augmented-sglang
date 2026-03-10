from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


IntentLabel = Literal["show_sql", "describe_schema", "explain_last", "nl2sql"]


class Clarification(BaseModel):
    missing: list[str] = Field(default_factory=list, description="Missing slots/fields that must be provided")
    question: str = Field(..., min_length=1, description="Clarifying question to ask the user")


class NL2SQLRouterDecision(BaseModel):
    action: Literal["run", "clarify"] = Field(..., description="Whether to run NL2SQL now or ask a clarification question")
    clarification: Clarification | None = Field(default=None, description="Present only when action=clarify")


class TimeRange(BaseModel):
    """Simple time range representation for NL2SQL planning.

    - kind=none: no time filter
    - kind=relative_days: last_days=N (e.g. 최근 7일)
    - kind=absolute: start/end are timestamps (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
    """
    kind: Literal["none", "relative_days", "absolute"] = "none"
    last_days: int | None = Field(default=None, ge=1, le=3650, description="Used when kind=relative_days")
    start: str | None = Field(default=None, description="Used when kind=absolute")
    end: str | None = Field(default=None, description="Used when kind=absolute")


FilterOp = Literal["EQ", "IN", "GT", "GE", "LT", "LE", "BETWEEN", "LIKE"]


class FilterCondition(BaseModel):
    field: str = Field(..., description="Canonical field name, e.g., asset_name, location, event_type, severity")
    op: FilterOp = Field(..., description="Comparison operator")
    value: str | int | float | None = Field(default=None, description="Single value (EQ/GT/...)")
    values: list[str | int | float] | None = Field(default=None, description="Multi values (IN/BETWEEN)")


NL2SQLTemplateId = Literal[
    "asset_events",
    "asset_downtime",
    "asset_avg_temperature",
    "topk_downtime_assets",
    "topk_temperature_assets",
    "recent_events",
]


class NL2SQLPlan(BaseModel):
    """LLM output schema (planner IR).

    Keep this intentionally small in early phases.
    """
    template_id: NL2SQLTemplateId

    # Common slots
    asset_name: str | None = Field(default=None, description="Equipment/asset name (e.g. ETCH-01)")
    top_k: int | None = Field(default=None, ge=1, le=100, description="Top-K for ranking templates")
    limit: int | None = Field(default=None, ge=1, le=200, description="Limit for list templates")
    time_range: TimeRange | None = Field(default=None)

    # Optional: generic filters (compile-time allowlist)
    filters: list[FilterCondition] = Field(default_factory=list)

    # Optional: debug-friendly plan fields
    group_by: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class QueryPrep(BaseModel):
    """Optional query preparation output.

    Goal: normalize diverse natural language questions into a canonical short form
    so downstream routing/templates become easier.
    """
    normalized_query: str = Field(..., min_length=1)
