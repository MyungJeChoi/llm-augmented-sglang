from __future__ import annotations

from typing import Any


def intent_messages(query: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "너는 라우터다. 사용자의 입력을 아래 4개 중 하나로만 분류한다.\n"
                "- show_sql: 마지막으로 실행된 SQL을 보여달라는 요청\n"
                "- describe_schema: 테이블/컬럼/스키마 구조를 물어보는 요청\n"
                "- explain_last: 직전 NL2SQL 실행(리라이트/캐시/KG)을 설명해달라는 요청\n"
                "- nl2sql: 실제 데이터 조회/질문(자연어 질의)\n\n"
                "반드시 라벨 문자열만 출력해라. 다른 텍스트/설명은 금지."
            ),
        },
        {"role": "user", "content": query},
    ]


def nl2sql_router_messages(query: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "너는 NL2SQL 실행 가능 여부를 판단하는 라우터다.\n"
                "사용자 질의가 불완전하여 추가 정보가 반드시 필요하면 action='clarify'를 선택하고,\n"
                "clarification.question에 사용자에게 물어볼 한 문장 질문을 작성한다.\n"
                "추가 정보가 없어도 합리적으로 실행 가능하면 action='run'을 선택한다.\n\n"
                "중요 규칙:\n"
                "- 'top/상위/랭킹/순위' 질의는 기준 지표(예: 다운타임, 온도)가 없으면 clarify.\n"
                "- '이벤트/로그'를 특정 장비에 대해 보려는데 장비명이 없으면 clarify.\n"
                "- 질문은 한국어로, 짧고 구체적으로.\n"
            ),
        },
        {"role": "user", "content": query},
    ]


def nl2sql_plan_messages(query: str, *, schema_summary: str, kg_context: dict | None = None) -> list[dict[str, Any]]:
    # schema_summary : 현재 db에 어떤 table이 있는지?
    # kg_context : 키워드(다운타임, 온도 등)의 정보를 찾을 때, 어떤 table, column을 참고해야 하는지?
    kg_text = ""
    if kg_context and isinstance(kg_context, dict):
        # Keep it short; just surface candidate mappings.
        mappings = kg_context.get("mappings") or []
        if isinstance(mappings, list) and mappings:
            # show up to 12 mappings
            pairs = []
            for m in mappings[:12]:
                raw = m.get("raw")
                can = m.get("canonical")
                col = m.get("col_id")
                if raw and col:
                    pairs.append(f"{raw}->{can}:{col}")
            if pairs:
                kg_text = "KG 후보 매핑(일부):\n" + "\n".join(f"- {p}" for p in pairs) + "\n"

    return [
        {
            "role": "system",
            "content": (
                "너는 NL2SQL '플래너'다. SQL을 직접 만들지 말고, 주어진 템플릿 중 하나를 선택하고\n"
                "필요한 슬롯(예: asset_name, top_k, limit, time_range 등)을 채워서 JSON으로만 출력한다.\n\n"
                "사용 가능한 template_id:\n"
                "- asset_events: 특정 자산의 이벤트/로그 목록\n"
                "- asset_downtime: 특정 자산의 다운타임 합(시간)\n"
                "- asset_avg_temperature: 특정 자산의 평균 온도\n"
                "- topk_downtime_assets: 다운타임 기준 자산 랭킹\n"
                "- topk_temperature_assets: 평균 온도 기준 자산 랭킹\n"
                "- recent_events: 최근 이벤트 목록(기본 fallback)\n\n"
                "주의:\n"
                "- 자산명이 명시되지 않았는데 특정 자산 템플릿을 선택하지 마라.\n"
                "- top_k/limit이 없으면 보수적으로 기본값을 사용해라(top_k=10, limit=20/50).\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"사용자 질의: {query}\n\n"
                f"DB 스키마 요약:\n{schema_summary}\n\n"
                f"{kg_text}"
                "위 정보를 참고해서 적절한 template_id와 파라미터를 JSON으로 출력해라."
            ),
        },
    ]


def query_prep_messages(query: str) -> list[dict[str, Any]]:
    # 과거 프롬프트(보존):
    # 너는 '질의 정규화기(query preparation)'다.
    # 사용자의 자연어 질의를, 우리 시스템이 처리하기 쉬운 짧은 정규 형태로 바꾼다.
    # 다음 규칙을 따른다:
    # - 의미를 바꾸지 말고, 핵심 엔티티/지표/기간/topK를 보존한다.
    # - 가능하면 아래 형태 중 하나로 정규화한다:
    #   * '<지표> 많은/높은 장비 topK'
    #   * '<자산명> 이벤트'
    #   * '<자산명> 다운타임'
    #   * '<자산명> 온도'
    #   * '최근 이벤트'
    # - 출력은 JSON만: {"normalized_query": "..."}
    return [
        {
            "role": "system",
            "content": (
                "너는 '질의 정규화기(query preparation)'다.\n"
                "사용자의 자연어 질의를, 우리 시스템이 처리하기 쉬운 짧은 정규 형태로 바꾼다.\n"
                "규칙은 엄격히 따른다.\n"
                "기본 규칙:\n"
                "- 의미를 바꾸지 말고, 핵심 엔티티/지표/기간/topK를 보존한다.\n"
                "- 출력은 JSON만: {\"normalized_query\": \"...\"}\n"
                "- 원본 질의에 나타난 자산명(예: ETCH-011, PUMP-007A, CK-01-03)은 철자, 대소문자 스타일, 하이픈/언더스코어 위치,\n"
                "  그리고 숫자 자릿수(선행 0 포함)를 그대로 보존한다.\n"
                "- 자산명은 요약이나 축약을 하지 않는다. e.g., ETCH-011은 ETCH-11로 바꾸지 않는다.\n"
                "- 자산명을 지우거나 바꿔치기하지 않는다. 불확실하면 질의를 그대로 유지한다.\n"
                "- 가능하면 아래 형태 중 하나로 정규화한다:\n"
                "  * '<지표> 많은 장비 topK'\n"
                "  * '<지표> 높은 장비 topK'\n"
                "  * '<자산명> 이벤트'\n"
                "  * '<자산명> 다운타임'\n"
                "  * '<자산명> 온도'\n"
                "  * '최근 이벤트'\n"
                "강제 예시:\n"
                "입력: ETCH-011 이벤트\n"
                "출력: {\"normalized_query\": \"ETCH-011 이벤트\"}\n"
                "입력: etch_011 다운타임 보여줘\n"
                "출력: {\"normalized_query\": \"ETCH-011 다운타임\"}\n"
                "입력: CMP-1 이벤트\n"
                "출력: {\"normalized_query\": \"CMP-1 이벤트\"}\n"
                "입력: ETCH-011 이벤트 개수\n"
                "출력: {\"normalized_query\": \"ETCH-011 이벤트\"}\n"
                "입력: 전체 장비 다운타임 top 5\n"
                "출력: {\"normalized_query\": \"다운타임 많은 장비 top5\"}\n"
                "입력: 전체 장비 온도 top 5\n"
                "출력: {\"normalized_query\": \"온도 높은 장비 top5\"}\n"
                "입력: 최근 이벤트\n"
                "출력: {\"normalized_query\": \"최근 이벤트\"}\n"
            ),
        },
        {"role": "user", "content": query},
    ]
