from __future__ import annotations

import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


class MetaStore:
    """Tiny key-value store backed by the same SQL database.

    We use it to store `kg_version` so that cache keys can include a version.
    When the KG changes, bump the version -> old cache entries become stale.

    This is intentionally lightweight.
    """

    def __init__(self, sqlalchemy_url: str):
        self._is_sqlite = sqlalchemy_url.startswith("sqlite")
        if self._is_sqlite:
            self.engine = create_engine(
                sqlalchemy_url,
                connect_args={"check_same_thread": False},
                poolclass=NullPool,
            )
        else:
            self.engine = create_engine(sqlalchemy_url, pool_pre_ping=True)

    def get(self, key: str, default: str | None = None) -> str | None:
        q = "SELECT value FROM kv_store WHERE key=:key"
        with self.engine.connect() as conn:
            row = conn.execute(text(q), {"key": key}).fetchone()
            return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        if self._is_sqlite:
            q = "INSERT OR REPLACE INTO kv_store(key, value, updated_at) VALUES (:key, :value, datetime('now'))"
            with self.engine.begin() as conn:
                conn.execute(text(q), {"key": key, "value": value})
            return

        # Postgres / others
        q = """
        INSERT INTO kv_store(key, value, updated_at)
        VALUES (:key, :value, NOW())
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
        """
        with self.engine.begin() as conn:
            conn.execute(text(q), {"key": key, "value": value})

    def bump_kg_version(self) -> str:
        new_ver = str(uuid.uuid4())
        self.set("kg_version", new_ver)
        return new_ver
