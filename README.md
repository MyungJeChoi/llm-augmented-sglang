# KG-backed NL2SQL Platform

- Goal: 실무형 NL2SQL 파이프라인을 **Neo4j(KG) + SQLite + FastAPI + LangGraph**로 재현  
- 로컬에서 바로 실행 가능한 상태를 유지하면서, 운영성(요청 추적, 감사, 변경이력, 캐시, 지표)까지 함께 실습할 수 있게 정리

---

## 현재 적용 상태 

- Single-turn NL2SQL: `POST /chat/query`
- Multi-turn 에이전트: `POST /agent/chat`
- Thread persistence: LangGraph + SQLite checkpointer (`data/agent_checkpoints.sqlite`)
- Request 관측:
  - `RequestIdMiddleware` → `X-Request-ID` 생성/전파
  - `/metrics` in-memory counters/latency snapshot
- Audit & Governance
  - `audit_logs` 기록
  - `kg_changes` 변경 이력(관리자 KG 조작) + `kv_store.kg_version` 버전 bump
- Cache: TTL + LRU (`enable_cache`, `cache_ttl_seconds`, `cache_max_items`)
- KG 운영: `/admin/validate`, `/admin/changes`
- LLM 통합(옵션)
  - Query Preparation
  - Intent 분류
  - NL2SQL 라우팅(clarification 필요 판정)
  - SQL plan 생성(템플릿 기반 컴파일)
- 운영 유틸: `api/tools/*` (로그/메트릭/헬스 확인)

---

## 0) Prerequisites

- Python 3.10+ (권장 3.12)
- 실행 중인 Neo4j
  - Bolt: `bolt://localhost:7687` 같은 값(또는 `17687` 등 custom port)
- (옵션) LLM 서버
  - SGLang: `http://localhost:38750`(또는 `/v1` 포함)
  - vLLM: OpenAI-compatible API

### 0.1 SGLang 설치/실행 가이드 (선택)

`SGLang`은 로컬 LLM 서버로 사용하므로, 프로젝트 핵심 의존성(`requirements.txt`)과는 분리해 설치하는 것을 권장합니다.

```bash
# 설치
pip install "sglang[all]>=0.4.6.post1"

# 기본 실행 (포트 예시: 38750)
python -m sglang.launch_server \
  --model-path Qwen/Qwen3-32B-AWQ \
  --host 0.0.0.0 \
  --port 38750
```

멀티 GPU 사용 예시:

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 \
python -m sglang.launch_server \
  --model-path Qwen/Qwen3-32B-AWQ \
  --host 0.0.0.0 \
  --port 38750 \
  --tensor-parallel-size 4
```

동작 확인:

```bash
curl -s -X POST "http://127.0.0.1:38750/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-32B-AWQ","messages":[{"role":"user","content":"hello"}],"max_tokens":16,"temperature":0}'
```

---

## 1) 프로젝트 설정

### 1.1 환경변수

루트에 `.env`를 만들고 아래 항목을 설정하세요.

```bash
APP_ENV=dev
ADMIN_API_KEY=change-me
ENABLE_CACHE=true
CACHE_TTL_SECONDS=60
CACHE_MAX_ITEMS=2048

SQLALCHEMY_DATABASE_URL=sqlite:///../data/app.db

NEO4J_BOLT_URL=bolt://localhost:7687
NEO4J_HTTP_URL=http://localhost:7474
NEO4J_USER=neo4j
NEO4J_PASSWORD=change-me

LANGGRAPH_CHECKPOINT_PATH=data/agent_checkpoints.sqlite

# LLM (모두 켜도 되지만, 단계별로 끌 수도 있음)
LLM_BACKEND=sglang
LLM_BASE_URL=http://localhost:38750
LLM_API_KEY=EMPTY
LLM_MODEL=Qwen/Qwen3-32B-AWQ
LLM_DISABLE_THINKING=true
LLM_ENABLE_QUERY_PREP=true
LLM_ENABLE_INTENT_ROUTER=true
LLM_ENABLE_NL2SQL_ROUTER=true
LLM_ENABLE_SQLGEN=true
LLM_PROMPT_VERSION=v1
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=512
LLM_TIMEOUT_S=30.0
```

### 1.2 Python 실행 환경

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.3 DB/시드 초기화

```bash
python scripts/init_sqlite.py      # data/app.db 생성 + 기본 seed
python scripts/seed_neo4j.py       # KG seed/constraints
```

### 1.4 서버 실행

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

---

## 2) 빠른 사용 예시

### 2.1 Health / Metrics

```bash
curl -s http://127.0.0.1:8001/health
curl -s http://127.0.0.1:8001/metrics | jq
```

### 2.2 NL2SQL (single turn)

```bash
curl -s -X POST "http://127.0.0.1:8001/chat/query" \
  -H "Content-Type: application/json" \
  -d '{"query":"다운타임 많은 장비 top10","actor":"me"}' | jq
```

반환 예시 키:  
- `request_id`, `kg_version`, `query_original`, `query_prepared`, `query_rewritten`
- `kg_context`, `sql`, `rows`, `timings_ms`, `cache`

### 2.3 Agent (multi-turn)

```bash
# 첫 턴: thread_id가 없으면 서버가 새로 생성
curl -s -X POST "http://127.0.0.1:8001/agent/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"정지시간 많은 장비 top10","actor":"me","debug":true}' | jq
```

```bash
# 후속 턴: thread_id 유지
THREAD_ID=<위 응답의 thread_id>
curl -s -X POST "http://127.0.0.1:8001/agent/chat" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"'"$THREAD_ID"'","message":"1등 장비 이벤트 보여줘","actor":"me","debug":true}' | jq
```

응답 포맷 예시:
- `thread_id`, `assistant_message`
- `request_id`
- `debug=true`일 때: `intent`, `pending`, `last_nl2sql_ok`, `messages` 등

### 2.4 Admin API (X-API-KEY)

```bash
export ADMIN_API_KEY=change-me
curl -s -X GET "http://127.0.0.1:8001/admin/kg/version" \
  -H "X-API-KEY: $ADMIN_API_KEY" | jq
```

```bash
curl -s -X GET "http://127.0.0.1:8001/admin/validate?limit=50" \
  -H "X-API-KEY: $ADMIN_API_KEY" | jq
```

```bash
curl -s -X GET "http://127.0.0.1:8001/admin/changes?limit=20" \
  -H "X-API-KEY: $ADMIN_API_KEY" | jq
```

```bash
# thread state debug/reset (필수: admin key)
curl -s "http://127.0.0.1:8001/agent/state/$THREAD_ID" \
  -H "X-API-KEY: $ADMIN_API_KEY" | jq
curl -s -X DELETE "http://127.0.0.1:8001/agent/state/$THREAD_ID" \
  -H "X-API-KEY: $ADMIN_API_KEY" | jq
```

---

## 3) 디렉터리 구조

```text
api/
  app/
    core/                # settings, auth
    middlewares/         # request id + latency
    ops/                 # in-memory metrics
    routers/             # /chat, /agent, /admin, /kg, /metrics, /health
    agent/               # LangGraph graph/runtime/utils
    llm/                 # OpenAI-compatible client + prompts + schemas + tasks
    services/            # sql/neo4j/audit/kg_change/meta/cache
    pipelines/
      nl2sql.py          # 파이프라인(재작성, KG 캐시, SQL 생성, 실행)
  scripts/               # init_sqlite.py, seed_neo4j.py, generate_sqlite_augmented.py
  tools/                 # 브라우저 없이 동작 점검용 스크립트
  requirements.txt
kg/
  init/                  # neo4j constraints + seed
db/
  sqlite/                # schema
runs/
eval/
```

---

## 4) API 한눈에 보기

- `GET /health` : 서비스 상태
- `GET /metrics` : in-memory metrics snapshot
- `POST /chat/query` : NL2SQL 단발 처리
- `POST /kg/subgraph` : KG 이웃 조회
- `POST /agent/chat` : 멀티턴 대화
- `GET /agent/state/{thread_id}` : (Admin) 스레드 상태 조회
- `DELETE /agent/state/{thread_id}` : (Admin) 스레드 상태 삭제
- `GET /admin/kg/version` : 현재 `kg_version`
- `GET /admin/validate` : KG validation 체크
- `GET /admin/changes` : 최근 `kg_changes` 조회

---

## 5) 공부용 체크리스트

- NL2SQL 파이프라인
  - `app/pipelines/nl2sql.py`
  - `app/services/{kg_client.py,sql_client.py,cache.py,audit.py}`
- LLM 병목 지점(옵션)
  - `app/llm/tasks.py`, `app/llm/client.py`, `app/llm/prompts.py`, `app/llm/schemas.py`
- LangGraph 오케스트레이션
  - `app/agent/graph.py`, `app/agent/runtime.py`, `app/agent/utils.py`
- 운영/감사
  - `app/ops/metrics.py`, `app/services/kg_change_log.py`, `app/services/meta_store.py`, `app/core/security.py`
- 보강 포인트
  - Postgres 지원, 분산 캐시 교체, 감사/메트릭 외부 수집(프로메테우스/OTEL), 벡터 기반 KG 검색(RAG) 확장
