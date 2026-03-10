# SGLang 서빙 가이드 (OpenAI-compatible)

이 프로젝트는 LLM 서버를 **OpenAI-compatible Chat Completions API**로 붙이는 방식이다.
SGLang(권장) 또는 vLLM을 쓸 수 있다.

> 핵심: 애플리케이션은 `LLM_BASE_URL`(서버 주소)만 알면 되고,
> 실제 추론/배치/TP 등은 SGLang 서버 쪽에서 결정한다.

---

## 1) 설치 (예시)

SGLang 설치는 보통 아래처럼 한다(환경/드라이버에 따라 달라질 수 있음).

```bash
pip install "sglang[all]>=0.4.6.post1"
```

---

## 2) 서버 실행 (기본)

SGLang 기본 포트는 **30000**이다.

```bash
python -m sglang.launch_server --model-path Qwen/Qwen3-32B-AWQ --host 0.0.0.0 --port 30000
```

---

## 3) 특정 GPU만 사용하기

예: 1번 GPU만 쓰고 싶으면:

```bash
CUDA_VISIBLE_DEVICES=1 python -m sglang.launch_server --model-path Qwen/Qwen3-32B-AWQ --host 0.0.0.0 --port 30000
```

멀티 GPU를 사용할 땐 예:

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 python -m sglang.launch_server --model-path Qwen/Qwen3-32B-AWQ --tensor-parallel-size 4 --host 0.0.0.0 --port 30000
```

---

## 4) Qwen3 thinking 비활성화(권장)

애플리케이션 레벨에서 `chat_template_kwargs={"enable_thinking": false}`를 요청에 포함시키는 방식(하드 스위치)을 권장한다.
또는 서버 실행 시 아예 non-thinking chat template을 지정할 수도 있다.

---

## 5) 프로젝트 환경변수(.env)

루트 `.env` 파일에 다음을 설정한다.

```bash
LLM_BACKEND=sglang
LLM_BASE_URL=http://localhost:30000
LLM_MODEL=Qwen/Qwen3-32B-AWQ
LLM_API_KEY=EMPTY
LLM_DISABLE_THINKING=true
```

스테이지별로 LLM 대체를 켜려면:

```bash
LLM_ENABLE_QUERY_PREP=true
LLM_ENABLE_INTENT_ROUTER=true
LLM_ENABLE_NL2SQL_ROUTER=true
LLM_ENABLE_SQLGEN=true
```

---

## 6) 헬스 체크

```bash
curl http://localhost:30000/v1/chat/completions -H "Content-Type: application/json" -d '{
  "model": "Qwen/Qwen3-32B-AWQ",
  "messages": [{"role":"user","content":"hello"}],
  "max_tokens": 16,
  "temperature": 0
}'
```
