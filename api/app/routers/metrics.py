from fastapi import APIRouter

from app.ops.metrics import metrics

router = APIRouter()


@router.get("/metrics")
def get_metrics():
    """Return in-memory metrics snapshot (JSON).

    For production, export Prometheus metrics instead.
    """

    return metrics.snapshot()
