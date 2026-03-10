from __future__ import annotations

import json

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


class KGChangeLogger:
    """Persist KG modification events (admin operations).

    This complements audit_logs:
    - audit_logs: request/response payload for API calls
    - kg_changes: governance-oriented records for KG edits (before/after + reason)
    """

    def __init__(self, sqlalchemy_url: str):
        if sqlalchemy_url.startswith("sqlite"):
            self.engine = create_engine(
                sqlalchemy_url,
                connect_args={"check_same_thread": False},
                poolclass=NullPool,
            )
        else:
            self.engine = create_engine(sqlalchemy_url, pool_pre_ping=True)

    def log(
        self,
        action: str,
        before: dict | None,
        after: dict | None,
        actor: str | None,
        request_id: str | None,
        reason: str | None = None,
        source_type: str | None = None,
    ) -> None:
        q = """
        INSERT INTO kg_changes(actor, request_id, action, before_payload, after_payload, reason, source_type)
        VALUES (:actor, :request_id, :action, :before_payload, :after_payload, :reason, :source_type)
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(q),
                {
                    "actor": actor,
                    "request_id": request_id,
                    "action": action,
                    "before_payload": json.dumps(before, ensure_ascii=False) if before is not None else None,
                    "after_payload": json.dumps(after, ensure_ascii=False) if after is not None else None,
                    "reason": reason,
                    "source_type": source_type,
                },
            )
