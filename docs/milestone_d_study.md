# Milestone D (Evaluation & Regression) Study Guide

Milestone D splits evaluation into **two lanes**:

1) **Toy data smoke (fast regression)**  
   - Goal: catch functional regressions quickly (E2E correctness, audit/metrics wiring, request_id propagation, agent pending flow).
   - Data: the default `data/app.db` produced by `scripts/init_sqlite.py`.
   - Runtime: ~10–30 seconds.

2) **Augmented data scale (performance & stability)**  
   - Goal: validate behavior under larger tables (latency distribution, cache behavior, audit persistence, no timeouts).
   - Data: a generated `data/app_large.db` (deterministic, seeded).
   - Runtime: depends on size (typically 1–5 minutes for moderate sizes).

## Quick start

### A. Toy smoke
1) Start services (Neo4j + API):
- Neo4j must be running (bolt URL in `.env`)
- API:
```bash
cd api
uvicorn app.main:app --host 127.0.0.1 --port 8750
```

2) Run smoke:
```bash
cd api
python tools/eval_milestone_d.py --scenario toy --host 127.0.0.1 --port 8750
```

Outputs are written under `runs/<timestamp>_<run_id>/`.

### B. Augmented scale
1) Generate large SQLite DB:
```bash
cd api
python scripts/generate_sqlite_augmented.py --db ../data/app_large.db --assets 200 --events-per-asset 80 --metrics-per-asset 200 --seed 42
```

2) Start API with the large DB (override env for the server process):
```bash
cd api
export SQLALCHEMY_DATABASE_URL=sqlite:///../data/app_large.db
uvicorn app.main:app --host 127.0.0.1 --port 8750
```

3) Run scale evaluation (make sure the eval process sees the same DB URL, or pass --sqlite-db):
```bash
cd api
export SQLALCHEMY_DATABASE_URL=sqlite:///../data/app_large.db
python tools/eval_milestone_d.py --scenario scale --host 127.0.0.1 --port 8750
```

## What gets measured

### Toy smoke
- `/health` ok
- `/chat/query`: NL2SQL runs and returns rows
- `/agent/chat`: (a) direct NL2SQL, (b) clarify -> follow-up, (c) rank follow-up
- `request_id` propagation: response header `X-Request-ID` == JSON `request_id`
- `audit_logs`: entries exist for NL2SQL executions (cache-miss path)
- `/metrics`: counters increase (http_requests_total, nl2sql_requests_total)

### Augmented scale
- DB row counts (assets/events/metrics) meet minimums
- Latency distribution (p50/p95) for chat/agent runs
- Cache behavior: repeated queries should increase cache-hit counters
- Batch asset queries: random assets sampled from DB should return rows without errors

## Notes
- Metrics are in-memory; a server restart resets them.
- Query-level cache can short-circuit NL2SQL and skip audit logging.
  The evaluator distinguishes cache-miss vs cache-hit cases.
