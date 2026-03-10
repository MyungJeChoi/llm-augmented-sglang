from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.pipelines.nl2sql import run_nl2sql_pipeline

router = APIRouter()


class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question")
    actor: str | None = None


@router.post("/query")
def query(req: QueryRequest, request: Request):
    rid = getattr(request.state, "request_id", None)
    return run_nl2sql_pipeline(req.query, actor=req.actor, request_id=rid)
