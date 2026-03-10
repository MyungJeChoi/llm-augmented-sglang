from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import require_admin_api_key
from app.services.audit import AuditLogger
from app.services.kg_change_log import KGChangeLogger
from app.services.kg_client import KGClient
from app.services.meta_store import MetaStore
from app.services.sql_client import SQLClient

router = APIRouter(dependencies=[Depends(require_admin_api_key)])


class _BaseAdminPayload(BaseModel):
    actor: str | None = Field(default=None, description="Who is making this change")
    reason: str | None = Field(default=None, description="Why this change is needed")
    source_type: str | None = Field(default="manual", description="manual | import | log_mining | llm_suggest")


class TermUpsert(_BaseAdminPayload):
    term_id: str
    text: str
    canonical: bool = True


class SynonymUpsert(_BaseAdminPayload):
    alias_term_id: str
    alias_text: str
    canonical_term_id: str
    canonical_text: str


class MappingUpsert(_BaseAdminPayload):
    term_text: str
    table: str
    column: str


@router.post("/term")
def upsert_term(req: TermUpsert, request: Request):
    rid = getattr(request.state, "request_id", None)
    audit = AuditLogger(settings.sqlalchemy_database_url)
    changes = KGChangeLogger(settings.sqlalchemy_database_url)
    meta = MetaStore(settings.sqlalchemy_database_url)

    kg = KGClient(settings.neo4j_bolt_url, settings.neo4j_user, settings.neo4j_password)
    try:
        before = kg.get_term_by_id(req.term_id)
        kg.upsert_term(req.term_id, req.text, req.canonical)
        after = kg.get_term_by_id(req.term_id)

        changes.log(
            action="term.upsert",
            before=before,
            after=after,
            actor=req.actor,
            request_id=rid,
            reason=req.reason,
            source_type=req.source_type,
        )
        new_ver = meta.bump_kg_version()

        payload = {"before": before, "after": after, "kg_version": new_ver}
        audit.log("admin.term.upsert", payload, actor=req.actor, request_id=rid)
        return {"ok": True, "kg_version": new_ver, "before": before, "after": after}
    finally:
        kg.close()


@router.post("/synonym")
def upsert_synonym(req: SynonymUpsert, request: Request):
    rid = getattr(request.state, "request_id", None)
    audit = AuditLogger(settings.sqlalchemy_database_url)
    changes = KGChangeLogger(settings.sqlalchemy_database_url)
    meta = MetaStore(settings.sqlalchemy_database_url)

    kg = KGClient(settings.neo4j_bolt_url, settings.neo4j_user, settings.neo4j_password)
    try:
        before_alias = kg.get_term_by_id(req.alias_term_id)
        before_can = kg.get_term_by_id(req.canonical_term_id)

        kg.upsert_synonym(req.alias_term_id, req.alias_text, req.canonical_term_id, req.canonical_text)

        after_alias = kg.get_term_by_id(req.alias_term_id)
        after_can = kg.get_term_by_id(req.canonical_term_id)

        before = {"alias": before_alias, "canonical": before_can}
        after = {"alias": after_alias, "canonical": after_can}

        changes.log(
            action="synonym.upsert",
            before=before,
            after=after,
            actor=req.actor,
            request_id=rid,
            reason=req.reason,
            source_type=req.source_type,
        )
        new_ver = meta.bump_kg_version()

        payload = {"before": before, "after": after, "kg_version": new_ver}
        audit.log("admin.synonym.upsert", payload, actor=req.actor, request_id=rid)

        return {"ok": True, "kg_version": new_ver, "before": before, "after": after}
    finally:
        kg.close()


@router.post("/mapping")
def upsert_mapping(req: MappingUpsert, request: Request):
    rid = getattr(request.state, "request_id", None)
    audit = AuditLogger(settings.sqlalchemy_database_url)
    changes = KGChangeLogger(settings.sqlalchemy_database_url)
    meta = MetaStore(settings.sqlalchemy_database_url)

    kg = KGClient(settings.neo4j_bolt_url, settings.neo4j_user, settings.neo4j_password)
    try:
        col_id = f"{req.table}.{req.column}"
        before = {
            "term": kg.get_term_by_text(req.term_text),
            "column": kg.get_column(col_id),
        }

        kg.upsert_mapping(req.term_text, req.table, req.column)

        after = {
            "term": kg.get_term_by_text(req.term_text),
            "column": kg.get_column(col_id),
        }

        changes.log(
            action="mapping.upsert",
            before=before,
            after=after,
            actor=req.actor,
            request_id=rid,
            reason=req.reason,
            source_type=req.source_type,
        )
        new_ver = meta.bump_kg_version()

        payload = {"before": before, "after": after, "kg_version": new_ver}
        audit.log("admin.mapping.upsert", payload, actor=req.actor, request_id=rid)

        return {"ok": True, "kg_version": new_ver, "before": before, "after": after}
    finally:
        kg.close()


@router.get("/kg/version")
def get_kg_version():
    meta = MetaStore(settings.sqlalchemy_database_url)
    ver = meta.get("kg_version", default="unknown")
    return {"kg_version": ver}


@router.get("/validate")
def validate_kg(limit: int = 50):
    kg = KGClient(settings.neo4j_bolt_url, settings.neo4j_user, settings.neo4j_password)
    try:
        checks = kg.validate(limit=limit)
        # lightweight status summary
        summary = {k: len(v) for k, v in checks.items()}
        return {"summary": summary, "checks": checks}
    finally:
        kg.close()


@router.get("/changes")
def list_recent_changes(limit: int = 50):
    sqlc = SQLClient(settings.sqlalchemy_database_url)
    rows = sqlc.query(
        """
        SELECT change_id, created_at, actor, request_id, action, reason, source_type,
               before_payload, after_payload
        FROM kg_changes
        ORDER BY change_id DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return {"rows": rows}
