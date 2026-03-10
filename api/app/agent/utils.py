from __future__ import annotations

import re
from typing import Any


# Very small domain heuristics (extend freely)
_METRIC_KEYWORDS = {
    "다운타임",
    "정지시간",
    "비가동",
    "가동중지",
    "온도",
    "temperature",
}

# Assets in this scaffold use names like ETCH-01, ETCH-02
_ASSET_RE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*[-_]?\d{2,}\b")


def _msg_content(msg: Any) -> str:
    """Return message content for LangChain message objects or dicts."""
    if msg is None:
        return ""
    if hasattr(msg, "content"):
        return str(getattr(msg, "content") or "")
    if isinstance(msg, dict):
        return str(msg.get("content") or "")
    return str(msg)


def last_user_text(state: dict) -> str:
    msgs = state.get("messages") or []
    if not msgs:
        return ""
    return _msg_content(msgs[-1]).strip()


def extract_asset_mentions(text: str) -> list[str]:
    if not text:
        return []
    return _ASSET_RE.findall(text.upper())


# def parse_rank_request(text: str) -> int | None:
#     """Return 1-based rank if the user asked for '1등/2등/첫번째/두번째/top1' etc."""
#     t = (text or "").lower()
#     if any(k in t for k in ["1등", "첫", "top1", "top 1", "1위"]):
#         return 1
#     if any(k in t for k in ["2등", "두", "top2", "top 2", "2위"]):
#         return 2
#     if any(k in t for k in ["3등", "세", "top3", "top 3", "3위"]):
#         return 3
#     return None

def parse_rank_request(text: str) -> int | None:
    """Return 1-based rank if the user asked for a ranking position."""
    import re

    t = (text or "").strip().lower()
    if not t:
        return None

    # 숫자 패턴: top 10, 상위10, 3등, 3위, 3번째 등
    number_patterns = [
        r"(?:top|탑)\s*-?\s*(\d+)",
        r"상위\s*(\d+)",
        r"(\d+)\s*등",
        r"(\d+)\s*위",
        r"(\d+)\s*번째",
    ]
    for pattern in number_patterns:
        m = re.search(pattern, t)
        if m:
            rank = int(m.group(1))
            return rank if rank > 0 else None

    # 한글 순서어 패턴 (긴 패턴 우선)
    korean_ordinals = [
        ("열두", 12),
        ("열한", 11),
        ("열", 10),
        ("아홉", 9),
        ("여덟", 8),
        ("일곱", 7),
        ("여섯", 6),
        ("다섯", 5),
        ("넷", 4),
        ("셋", 3),
        ("둘", 2),
        ("첫", 1),
        ("열번째", 10),
        ("아홉번째", 9),
        ("여덟번째", 8),
        ("일곱번째", 7),
        ("여섯번째", 6),
        ("다섯번째", 5),
        ("네번째", 4),
        ("셋째", 3),
        ("둘째", 2),
        ("첫째", 1),
        ("첫번째", 1),
        ("두번째", 2),
        ("세번째", 3),
    ]
    for key, value in korean_ordinals:
        if key in t:
            return value

    return None


def pick_asset_from_last_result(last_nl2sql: dict | None, rank_1based: int = 1) -> str | None:
    if not last_nl2sql or not last_nl2sql.get("ok"):
        return None
    rows = last_nl2sql.get("rows") or []
    if not rows:
        return None
    idx = max(rank_1based - 1, 0)
    if idx >= len(rows):
        return None
    # Common column names in our SQL templates
    for key in ("asset_name", "asset", "asset_id"):
        if key in rows[idx]:
            return str(rows[idx][key])
    return None


def needs_metric_clarification(query: str) -> bool:
    """Heuristic: 'top10/상위/랭킹' queries need a metric (downtime/temp/etc)."""
    q = (query or "").strip()
    if not q:
        return False

    # 순위를 물어보는 / top10 등을 물어보는 질문이 아니면, metric을 확인할 필요 없음
    top_like = any(k in q.lower() for k in ["top", "상위", "랭킹", "순위"])
    if not top_like:
        return False

    has_metric = any(k in q for k in _METRIC_KEYWORDS)
    return not has_metric


def merge_clarification(pending_base: str, clarification: str) -> str:
    """Combine a previous ambiguous query with the user's clarification.

    We keep it intentionally simple: prepend clarification.
    Example: base="장비 top10", clarification="다운타임" -> "다운타임 장비 top10"
    """
    base = (pending_base or "").strip()
    clar = (clarification or "").strip()
    if not base:
        return clar
    if not clar:
        return base
    return f"{clar} {base}".strip()


def serialize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Serialize LangChain message objects into JSON-friendly dicts."""
    out: list[dict[str, Any]] = []
    for m in messages or []:
        if isinstance(m, dict):
            # already JSON-ish
            out.append({
                "role": m.get("role") or m.get("type"),
                "content": m.get("content"),
            })
            continue

        role = getattr(m, "type", None) or getattr(m, "role", None) or m.__class__.__name__
        content = _msg_content(m)
        out.append({"role": role, "content": content})
    return out
