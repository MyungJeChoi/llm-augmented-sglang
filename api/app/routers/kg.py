from fastapi import APIRouter, Query
from app.services.kg_client import KGClient
from app.core.config import settings

router = APIRouter()

@router.get("/subgraph")
def subgraph(term: str = Query(..., description="term text (e.g., 다운타임)"), limit: int = 20):
    kg = KGClient(settings.neo4j_bolt_url, settings.neo4j_user, settings.neo4j_password)
    try:
        return {"term": term, "neighbors": kg.get_term_neighbors(term, limit=limit)}
    finally:
        kg.close()
