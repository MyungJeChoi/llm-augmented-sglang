from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.core.config import settings

# LangGraph imports
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agent.graph import build_agent_graph


_REPO_ROOT = Path(__file__).resolve().parents[3]

# Process-wide singleton (good enough for a single-worker dev server)
_LOCK = threading.Lock()
_GRAPH: Any | None = None
_CHECKPOINTER: SqliteSaver | None = None
_CONN: sqlite3.Connection | None = None


def _checkpoint_db_path() -> Path:
    p = Path(settings.langgraph_checkpoint_path)
    return p if p.is_absolute() else (_REPO_ROOT / p)


def get_agent_graph():
    """Return a compiled LangGraph instance with SQLite-backed persistence."""
    global _GRAPH, _CHECKPOINTER, _CONN

    with _LOCK:
        if _GRAPH is not None:
            return _GRAPH

        db_path = _checkpoint_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # IMPORTANT:
        # - check_same_thread=False is safe here because SqliteSaver uses a lock internally.
        # - This is still meant for light workloads.
        _CONN = sqlite3.connect(str(db_path), check_same_thread=False)
        _CHECKPOINTER = SqliteSaver(_CONN)

        _GRAPH = build_agent_graph(checkpointer=_CHECKPOINTER)
        return _GRAPH


def delete_thread(thread_id: str) -> None:
    """Delete all checkpoints for a thread.

    Useful when you want to restart a conversation without restarting the server.
    """
    graph = get_agent_graph()

    # Access the checkpointer used by the graph.
    # The compiled graph exposes it as `.checkpointer`.
    cp = getattr(graph, "checkpointer", None)
    if cp is None:
        return
    try:
        cp.delete_thread(thread_id)
    except Exception:
        # best-effort cleanup; do not crash admin endpoint
        return


def close_agent_resources() -> None:
    """Close the SQLite connection (optional)."""
    global _GRAPH, _CHECKPOINTER, _CONN
    with _LOCK:
        _GRAPH = None
        _CHECKPOINTER = None
        if _CONN is not None:
            try:
                _CONN.close()
            finally:
                _CONN = None
