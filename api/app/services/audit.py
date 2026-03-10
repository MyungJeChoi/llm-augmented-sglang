from __future__ import annotations

import json
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

class AuditLogger:
    def __init__(self, sqlalchemy_url: str):
        self._is_postgres = sqlalchemy_url.startswith("postgresql")
        if sqlalchemy_url.startswith("sqlite"):
            self.engine = create_engine(
                sqlalchemy_url,
                connect_args={"check_same_thread": False},
                poolclass=NullPool,
            )
        else:
            self.engine = create_engine(sqlalchemy_url, pool_pre_ping=True)

    def log(self, action: str, payload: dict, actor: str | None = None, request_id: str | None = None):
        payload_expr = ":payload::jsonb" if self._is_postgres else ":payload"
        q = f"""INSERT INTO audit_logs(actor, request_id, action, payload)
                 VALUES (:actor, :request_id, :action, {payload_expr})"""
        with self.engine.begin() as conn:
            conn.execute(text(q), {
                "actor": actor,
                "request_id": request_id,
                "action": action,
                "payload": json.dumps(payload, ensure_ascii=False),
            })
