# Milestone B Study Guide (No-Docker)

이 문서는 "코드를 읽으면서" Milestone B의 핵심 운영 요소를 흡수하기 위한 체크리스트입니다.

---

## 0) 목표를 한 문장으로

> "NL2SQL"이 **돌아가는 것**(Milestone A)에서 끝내지 않고,
> "운영 가능한 서비스"처럼 보이게 만드는 **관측/감사/권한/변경이력/정합성/캐시**를 붙인다.

---

## 1) Request lifecycle / Request ID

### 코드를 볼 곳
- `api/app/middlewares/request_id.py`

### 이해해야 할 것
- Request/Response에 동일한 request_id를 붙이는 이유
  - 로그 correlation
  - 재현 가능성
- `X-Request-ID`를 클라이언트가 주입할 수 있게 해두면,
  - 상위 게이트웨이/프록시/배치 job과 trace를 연결할 수 있음

### 실습
- `/chat/query`에 `-H "X-Request-ID: test-123"`로 보내고 응답 헤더 확인

---

## 2) Observability (metrics)

### 코드를 볼 곳
- `api/app/ops/metrics.py`
- `api/app/routers/metrics.py`

### 이해해야 할 것
- 어떤 지표가 최소 세트인지
  - QPS/에러율/latency(p50/p95가 이상적)
  - cache hit ratio
- 지금 스캐폴드는 in-memory(프로세스 단위)
  - 운영에서는 Prometheus 같은 외부 시스템으로 export

### 실습
- 질의 20번 반복 후 `/metrics` snapshot 관찰

---

## 3) Audit Log vs Change Log

### 코드를 볼 곳
- `api/app/services/audit.py`
- `api/app/services/kg_change_log.py`
- SQLite: `db/sqlite/sqlite_schema.sql`

### 구분
- audit_logs: "요청 단위"(what happened)
  - query, context, sql, rows, error
- kg_changes: "지식 변경 단위"(why it changed)
  - before/after, reason, source_type

### 실습
- `/admin/synonym` 호출 → `kg_changes` 기록 확인 (`/admin/changes`)

---

## 4) KG Versioning & Cache Invalidation

### 코드를 볼 곳
- `api/app/services/meta_store.py`
- `api/app/pipelines/nl2sql.py`

### 이해해야 할 것
- cache key에 `kg_version`을 넣는 이유
  - KG가 바뀌면 기존 cache는 사실상 오염된 결과
  - version bump는 강제 invalidation과 동일

### 실습
1) 동일 query 2번 호출 → 두 번째가 query_cache hit인지 확인
2) `/admin/synonym` 등 KG 변경 → kg_version 바뀜
3) 다시 동일 query 호출 → cache miss로 재계산되는지 확인

---

## 5) Admin Auth (X-API-KEY)

### 코드를 볼 곳
- `api/app/core/security.py`
- `api/app/routers/admin.py`

### 이해해야 할 것
- 최소형 보안의 목적: "운영 요소가 있다"를 보여주기
- 실제 제품에서는:
  - 사용자/그룹/RBAC
  - 키 회전
  - 감사 추적 강화

---

## 6) KG Validation

### 코드를 볼 곳
- `api/app/services/kg_client.py`의 `validate()`

### 지금 들어간 체크(예시)
- terms_without_mapping
- synonyms_to_non_canonical
- orphan_columns

### 다음 확장 아이디어
- canonical term 중복(text)
- synonym cycle 검출
- mapping coverage KPI(coverage%, unmapped terms count)

---

## 7) NL2SQL 품질 개선 루프

### 코드를 볼 곳
- `api/app/pipelines/nl2sql.py`

### 지금 구조의 핵심
- rewrite -> kg_context -> sqlgen -> validate -> execute
- 실패 시 `nl2sql.error`로 기록

### 다음 단계(권장)
- `generate_sql_mock()`를 LLM 호출로 교체
  - 입력: schema + kg_context + user query
  - 출력: SQL
  - guardrail: validate_sql + LIMIT 강제
- error taxonomy 기반 재시도
  - schema mismatch → context 확장
  - empty result → filter 완화

---

## 8) Milestone B 완료 기준(Done)

- [ ] 모든 API 응답에 request_id가 있다
- [ ] `/metrics`에서 요청 수/에러 수/latency가 보인다
- [ ] `audit_logs`에 query/error가 남는다
- [ ] `/admin/*`는 키 없으면 막힌다
- [ ] KG 변경은 `kg_changes`에 before/after+reason가 남는다
- [ ] KG 변경 후 cache가 자동으로 무효화된다(kg_version)
