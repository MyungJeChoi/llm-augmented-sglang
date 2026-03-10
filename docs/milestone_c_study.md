# Milestone C Study Guide — LangGraph Multi-turn Agent

> 목표: Milestone B의 **(KG + NL2SQL + 운영요소)** 위에, 멀티턴 "에이전트" 레이어를 얹어
> - 상태 유지(thread_id)
> - 툴 선택 / 분기
> - clarification(질문 되묻기)
> - follow-up(이전 결과를 근거로 후속 질의)
> 를 **코드로 명시적으로 표현**하는 연습을 합니다.

이 스캐폴드는 LLM 없이도 동작하도록 **휴리스틱 기반**으로 구성되어 있습니다.
나중에 LLM router/tool-calling로 교체하기 쉬운 형태로 경계면을 잡았습니다.

---

## 1) 핵심 컨셉

### 1.1 thread_id
- LangGraph는 `thread_id`를 기준으로 **대화 상태(state)** 를 저장/복구합니다.
- 이 프로젝트에서 `/agent/chat`은 요청 body의 `thread_id`가 **있으면 이어서**, 없으면 **새 thread 생성**합니다.

### 1.2 MessagesState (+ add_messages reducer)
- state 안에 `messages`(대화 히스토리)를 유지합니다.
- "리듀서"(reducer)가 있어서, 노드가 반환하는 `{"messages": [...]}` 업데이트가 **append**로 누적됩니다.

### 1.3 checkpointer (SqliteSaver)
- `langgraph.checkpoint.sqlite.SqliteSaver`를 사용해 상태를 SQLite DB에 저장합니다.
- 기본 경로: `data/agent_checkpoints.sqlite`
- 장점: 서버 재시작해도 대화 복구 가능
- 단점: SQLite 기반이라 고부하/멀티워커에는 부적합(데모/소규모 용도)

---

## 2) 실행 순서 (Milestone C)

Milestone B와 동일한 선행조건 + LangGraph 의존성 추가.

### 2.1 설치/초기화
```bash
cd kg_nl2sql_platform
cp .env.example .env

cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/init_sqlite.py
python scripts/seed_neo4j.py
```

### 2.2 서버 실행
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

---

## 3) 코드 읽는 순서 (추천)

### Step 0 — 엔드포인트부터 진입
- `app/routers/agent.py`
  - `/agent/chat`: 멀티턴 대화
  - `/agent/state/{thread_id}`: 현재 상태 조회(디버그, admin key 필요)
  - `/agent/state/{thread_id}` DELETE: thread 리셋

여기서 확인할 포인트:
- `config = {"configurable": {"thread_id": ...}, "metadata": {...}}`
- `graph.invoke({"messages": [{"role": "user", "content": ...}]}, config)`

### Step 1 — runtime: checkpointer + graph singleton
- `app/agent/runtime.py`
  - SQLite connection 열기
  - `SqliteSaver(conn)` 생성
  - `build_agent_graph(checkpointer=...)`로 그래프 compile

확인할 포인트:
- `LANGGRAPH_CHECKPOINT_PATH`가 어디에 쓰이는지
- SQLite 파일이 실제로 생성되는지

### Step 2 — graph: 노드와 분기
- `app/agent/graph.py`

노드 구성:
1) `prepare`
   - pending clarification merge
   - follow-up 처리(`1등 장비 이벤트` → 이전 결과에서 자산 추론)
2) `classify_intent`
   - show_sql / describe_schema / explain_last / nl2sql
3) `nl2sql_router`
   - `top10`인데 지표가 없으면 질문 되묻기
4) `run_nl2sql`
   - `run_nl2sql_pipeline()` 호출
5) `show_sql` / `describe_schema` / `explain_last`
   - 멀티턴에서 자주 필요한 "툴" 느낌의 보조 기능

### Step 3 — 기존 파이프라인과의 연결
- `app/pipelines/nl2sql.py`
  - Milestone C에서 **asset-specific 템플릿**이 추가되어, 예) `ETCH-01 이벤트` 같은 후속 질의가 조금 더 자연스러워졌습니다.

---

## 4) 기능 테스트 시나리오

### 4.1 Clarification (지표 누락)
```bash
curl -s -X POST http://127.0.0.1:8001/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"장비 top10","debug":true}' | jq
```

예상:
- 에이전트가 "지표가 필요"하다고 질문
- response에 `pending.reason = need_metric`

그 다음:
```bash
THREAD_ID=<위 응답 thread_id>
curl -s -X POST http://127.0.0.1:8001/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"thread_id":"'"$THREAD_ID"'","message":"다운타임","debug":true}' | jq
```

예상:
- `current_query`가 `다운타임 장비 top10` 형태로 merge되어 NL2SQL 수행

### 4.2 Follow-up (랭크 참조)
```bash
curl -s -X POST http://127.0.0.1:8001/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"다운타임 많은 장비 top10"}' | jq
```

`thread_id`를 유지한 채:
```bash
curl -s -X POST http://127.0.0.1:8001/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"thread_id":"...","message":"1등 장비 이벤트 보여줘"}' | jq
```

예상:
- 이전 TOP 결과에서 1등 자산명을 추론 → `ETCH-01 이벤트`로 질의

---

## 5) Milestone C를 "연구/실무"로 발전시키는 확장 아이디어

### 5.1 휴리스틱 → LLM Router로 교체
- `classify_intent()`를 LLM 기반 router로 교체
- prompt에 사용할 것:
  - 최근 `messages`
  - 테이블 스키마
  - KG context(terms/mappings)

### 5.2 Tool calling
- NL2SQL, schema 설명, 상태 조회 등을 "툴"로 정의
- LLM이 tool을 선택하고 결과를 종합하도록 구성

### 5.3 Guardrails
- SQL 검증 강화(파라미터 바인딩 / whitelist / AST parse)
- query budget, recursion limit, latency budget

### 5.4 Evaluation
- (정량) 질의별 정답 SQL/결과를 gold로 두고 regression test
- (정성) clarification 질문 품질, follow-up 성공률

---

## 6) 체크리스트

- [ ] `data/agent_checkpoints.sqlite`가 생성되는지
- [ ] 서버 재시작 후에도 같은 `thread_id`로 상태가 복구되는지
- [ ] `top10`에서 지표 누락 시 질문이 나가는지
- [ ] `1등 장비 ...` follow-up이 의도한 자산을 집는지
- [ ] `/agent/state/{thread_id}` 조회가 되는지 (admin key)
