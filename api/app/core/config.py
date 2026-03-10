from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve repository root regardless of current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Central configuration.

    Notes:
    - We intentionally set extra="ignore" so local experimentation doesn't break
      when you add extra fields to .env.
    """

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    # API
    app_env: str = "dev"

    # Auth (admin)
    admin_api_key: str = "change-me"

    # Cache
    enable_cache: bool = True
    cache_ttl_seconds: int = 60
    cache_max_items: int = 2048

    # SQL (SQLite in this no-docker scaffold)
    sqlalchemy_database_url: str

    # Neo4j
    neo4j_bolt_url: str
    neo4j_http_url: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str = "DSproject!!"

    # LangGraph (Milestone C)
    # - Use a SQLite-backed checkpointer so conversation state survives restarts.
    # - Path is resolved relative to repo root by default.
    langgraph_checkpoint_path: str = "data/agent_checkpoints.sqlite"

    # Agent behavior (heuristics in this scaffold)
    agent_max_history_turns: int = 20


    # -----------------
    # LLM (OpenAI-compatible server: SGLang / vLLM)
    # -----------------
    # Enable per-stage gradually (recommended).
    llm_enable_query_prep: bool = True
    llm_enable_intent_router: bool = True
    llm_enable_nl2sql_router: bool = True
    llm_enable_sqlgen: bool = True

    # LLM backend hint: 'sglang' | 'vllm' | 'auto'
    llm_backend: str = "sglang"

    # Server endpoint (OpenAI-compatible)
    # - SGLang default: http://localhost:30000 (see docs)
    # - vLLM example:  http://localhost:8001
    # - The client will append "/v1" automatically if missing.
    llm_base_url: str = "http://localhost:38750"

    # Most self-hosted servers don't require a real key by default; keep a placeholder for compatibility.
    llm_api_key: str = "EMPTY"

    # Model id as served by the backend (can be HF repo id)
    llm_model: str = "Qwen/Qwen3-32B-AWQ"

    # Sampling / generation
    llm_temperature: float = 0.0
    llm_max_tokens: int = 512

    # Networking
    llm_timeout_s: float = 30.0

    # Prompting / rollout
    llm_prompt_version: str = "v1"

    # Qwen3 reasoning tends to add "thinking" text; disabling reduces JSON breakage.
    llm_disable_thinking: bool = True


settings = Settings()
