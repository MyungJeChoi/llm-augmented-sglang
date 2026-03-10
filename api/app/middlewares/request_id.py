from __future__ import annotations

import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.ops.metrics import metrics


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request_id to every request/response.

    - Reads X-Request-ID if present, otherwise generates a UUID4
    - Sets response header X-Request-ID
    - Records a coarse request latency metric
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid

        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        except Exception:
            metrics.inc("http_errors_total", 1)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            metrics.inc("http_requests_total", 1)
            metrics.observe_http_latency(elapsed_ms)
