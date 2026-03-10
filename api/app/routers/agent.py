from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agent.runtime import get_agent_graph, delete_thread
from app.agent.utils import serialize_messages
from app.core.security import require_admin_api_key

router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    thread_id: str | None = Field(default=None, description="Conversation thread id. If omitted, a new one is created.")
    actor: str | None = Field(default=None, description="Optional actor/user id for audit logs")
    debug: bool = Field(default=False, description="Include extra debug fields in the response")


@router.post("/chat")
def chat(req: AgentChatRequest, request: Request):
    """Multi-turn chat endpoint backed by LangGraph.

    - `thread_id` is the key. Reuse the same thread_id to continue the conversation.
    - The agent uses the Milestone B NL2SQL pipeline as a tool.
    """

    rid = getattr(request.state, "request_id", None)
    thread_id = req.thread_id or str(uuid.uuid4())

    graph = get_agent_graph()

    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": {"request_id": rid, "actor": req.actor},
    }

    # Provide only the new message plus request context for downstream audit logging.
    # The checkpointer merges this patch into the existing thread state.
    final_state = graph.invoke(
        {
            "messages": [{"role": "user", "content": req.message}],
            "request_id": rid,
            "actor": req.actor,
        },
        config,
    )

    msgs = final_state.get("messages") or []
    assistant_text = ""
    if msgs:
        assistant_text = getattr(msgs[-1], "content", None) or ""
        assistant_text = str(assistant_text)

    resp = {
        "ok": True,
        "request_id": rid,
        "thread_id": thread_id,
        "assistant_message": assistant_text,
    }

    if req.debug:
        resp.update(
            {
                "intent": final_state.get("intent"),
                "current_query": final_state.get("current_query"),
                "pending": final_state.get("pending"),
                "last_nl2sql_ok": (final_state.get("last_nl2sql") or {}).get("ok"),
                "last_nl2sql": final_state.get("last_nl2sql"),
                "messages": serialize_messages(msgs),
            }
        )

    return resp


@router.get("/state/{thread_id}", dependencies=[Depends(require_admin_api_key)])
def get_state(thread_id: str):
    """Debug endpoint: fetch the current thread state (admin only)."""

    graph = get_agent_graph()
    config = {"configurable": {"thread_id": thread_id}}
    snap = graph.get_state(config)

    values = snap.values or {}
    msgs = values.get("messages") or []

    # Only return a small, JSON-safe subset.
    return {
        "thread_id": thread_id,
        "next": list(snap.next) if getattr(snap, "next", None) else [],
        "values": {
            "intent": values.get("intent"),
            "current_query": values.get("current_query"),
            "pending": values.get("pending"),
            "last_nl2sql_ok": (values.get("last_nl2sql") or {}).get("ok"),
            "messages": serialize_messages(msgs),
        },
    }


@router.delete("/state/{thread_id}", dependencies=[Depends(require_admin_api_key)])
def reset_thread(thread_id: str):
    """Delete checkpoints for a thread_id (admin only)."""

    delete_thread(thread_id)
    return {"ok": True, "thread_id": thread_id}
