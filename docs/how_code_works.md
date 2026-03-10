## Multi-Turn Query (Heuristic Version)
1. query 입력  
   예시1 : 완전한 query  
   ```bash
    curl -s -X POST http://127.0.0.1:8001/agent/chat \
    -H 'Content-Type: application/json' \
    -d '{"message":"다운타임 장비 top10","debug":true}' | jq
    ```
   예시2 : 불완전한 query  
   ```bash
    curl -s -X POST http://127.0.0.1:8001/agent/chat \
    -H 'Content-Type: application/json' \
    -d '{"message":"장비 top10","debug":true}' | jq
    ```
2. chat 함수에서,  
   - `rid`(request_id), `thread_id`(query 입력 or uuid 생성) 등을 담은 config 생성
   - langgraph로 생성한 graph에 따라 query를 처리

3. graph invoke 과정  
   - graph.invoke()가 호출되면, 입력한 `thread_id`로 **이전 state을 로드**한다.
   - `prepare` : 현 `state`의 `current query` update
     - pending이 있는 경우 (불완전한 query가 이 thread_id를 타고 들어왔음),  
       (정상적인 추가 입력 기준) merge된 query update, pending 비움
     - pending이 없는 경우,  
       사전 규칙에 따라 query 가공 후 `current_query` update
   - `classify` : query의 요구사항 확인 (이 예제에서는 Heuristic하게 경우를 나눔)  
     - `show_sql`: 마지막 NL2SQL 결과의 SQL 원문 확인
     - `describe_schema`: SQLite schema 요약 (어떤 테이블이 있는지?)
     - `explain_last`: 마지막 NL2SQL 실행의 metadata를 요약
     - `nl2sql`: `nl2sql_router`을 통해 NL2SQL 바로 실행할지, or clarification 요구할지 판단  
       → `run_nl2sql`: 정상적인 query에 한해 nl2sql pipeline 실행

4. LLM으로 교체 대상
   1. query preparation: 현재는 고정된 형태(예. 온도 장비 top1) input만 처리가 가능
   2. query classification 
   3. nl2sql router
   4. nl2sql template

## Evaluation
### Metrics
1. /health 확인
2. /chat/query(단발성 query) 2종 확인 (다운타임 TOP, 온도 TOP)
3. /agent/chat(multi-turn) 3종 확인 