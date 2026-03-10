from __future__ import annotations

import threading
from collections import Counter, deque


class Metrics:
    """In-memory metrics collector.

    Intentionally minimal (no external deps).

    What we track:
    - http_* : request-level counts + coarse latency (middleware)
    - nl2sql_* : pipeline-level counts + stage latencies

    For production, export Prometheus metrics.
    """

    def __init__(self, latency_window: int = 200):
        self._lock = threading.Lock()
        self.counters = Counter()

        self.http_latency_ms = deque(maxlen=latency_window)
        self.nl2sql_latency_ms = deque(maxlen=latency_window)

        self.stage_latency_ms = {
            "rewrite": deque(maxlen=latency_window),
            "kg_context": deque(maxlen=latency_window),
            "sqlgen": deque(maxlen=latency_window),
            "execute": deque(maxlen=latency_window),
        }

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self.counters[name] += value

    def observe_http_latency(self, total_ms: float) -> None:
        with self._lock:
            self.http_latency_ms.append(float(total_ms))

    def observe_nl2sql_latency(self, total_ms: float, stages: dict[str, float] | None = None) -> None:
        with self._lock:
            self.nl2sql_latency_ms.append(float(total_ms))
            if stages:
                for k, v in stages.items():
                    if k in self.stage_latency_ms:
                        self.stage_latency_ms[k].append(float(v))

    def snapshot(self) -> dict:
        with self._lock:
            def _avg(dq):
                return (sum(dq) / len(dq)) if dq else 0.0

            return {
                "counters": dict(self.counters),
                "http_latency_ms": {
                    "count": len(self.http_latency_ms),
                    "avg": _avg(self.http_latency_ms),
                    "max": max(self.http_latency_ms) if self.http_latency_ms else 0.0,
                },
                "nl2sql_latency_ms": {
                    "count": len(self.nl2sql_latency_ms),
                    "avg": _avg(self.nl2sql_latency_ms),
                    "max": max(self.nl2sql_latency_ms) if self.nl2sql_latency_ms else 0.0,
                },
                "stage_latency_ms": {
                    k: {
                        "count": len(dq),
                        "avg": _avg(dq),
                        "max": max(dq) if dq else 0.0,
                    }
                    for k, dq in self.stage_latency_ms.items()
                },
            }


metrics = Metrics()
