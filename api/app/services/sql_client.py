from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

class SQLClient:
    """Thin SQL runner. Uses SQLAlchemy for portability.

    Notes for SQLite:
    - FastAPI runs sync handlers in a threadpool; SQLite connections are thread-bound by default.
      We set check_same_thread=False and NullPool to keep things simple for the MVP.
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

    def query(self, sql: str, params: dict | None = None):
        with self.engine.connect() as conn:
            res = conn.execute(text(sql), params or {})
            rows = [dict(r._mapping) for r in res.fetchall()]
            return rows
