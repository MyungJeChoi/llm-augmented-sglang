from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import requests


# --- parsing helpers ---------------------------------------------------------

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


class LLMError(RuntimeError):
    """Base exception for LLM integration errors."""


class LLMHTTPError(LLMError):
    def __init__(self, status_code: int, message: str, *, body: Any | None = None):
        super().__init__(f"LLM HTTP {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class LLMParseError(LLMError):
    pass


@dataclass(frozen=True)
class ChatResult:
    content: str
    raw: dict
    latency_ms: float


def _strip_wrappers(text: str) -> str:
    """Remove common wrappers that break JSON parsing (think blocks, code fences)."""
    t = (text or "").strip()
    if not t:
        return t

    # Remove <think>...</think> if present (reasoning models sometimes leak it).
    t = _THINK_BLOCK_RE.sub("", t).strip()

    # If the model wrapped JSON with ```json ... ```, unwrap it.
    m = _CODE_FENCE_RE.search(t)
    if m and m.group(1):
        inner = m.group(1).strip()
        # prefer the fenced block if it contains an object-ish start
        if "{" in inner:
            t = inner

    return t.strip()


def _extract_first_json_obj(text: str) -> dict:
    """Best-effort JSON object extraction from a model string output."""
    t = _strip_wrappers(text)
    if not t:
        raise LLMParseError("empty content")

    # Fast path: already a JSON object
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Robust scan using JSONDecoder.raw_decode on each '{' position.
    decoder = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(t[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue

    raise LLMParseError("no JSON object found in content")


def _normalize_choice(s: str) -> str:
    """Normalize a single-label output (strip whitespace/quotes)."""
    return (s or "").strip().strip('"').strip("'").strip()


# --- client ------------------------------------------------------------------


Backend = Literal["sglang", "vllm", "auto"]


class OpenAICompatClient:
    """Very small HTTP client for OpenAI-compatible Chat Completions endpoints.

    Target servers:
    - SGLang (recommended for your setup)
    - vLLM (also compatible)

    Notes:
    - `response_format={"type":"json_schema", ...}` is supported by SGLang Structured Outputs docs.
    - Non-standard fields (e.g., regex/ebnf/chat_template_kwargs) are passed via `extra_body`
      and merged into the request JSON, following the convention used by the OpenAI Python SDK.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        backend: Backend = "sglang",
        timeout_s: float = 30.0,
        disable_thinking: bool = True,
        default_extra_body: dict[str, Any] | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or "EMPTY"
        self.model = model
        self.timeout_s = float(timeout_s)
        self.disable_thinking = bool(disable_thinking)
        self.default_extra_body = dict(default_extra_body or {})

        b = (backend or "auto").strip().lower()
        if b not in {"sglang", "vllm", "auto"}:
            raise ValueError(f"invalid backend={backend!r}; expected 'sglang'|'vllm'|'auto'")
        self.backend: Backend = b  # type: ignore[assignment]

    def _url(self, path: str) -> str:
        # Allow user to pass either ".../v1" or root base.
        b = self.base_url
        if b.endswith("/v1"):
            return f"{b}{path}"
        return f"{b}/v1{path}"

    def chat_completions(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Call POST /chat/completions."""
        url = self._url("/chat/completions")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }

        # Qwen3 thinking can be disabled with chat_template_kwargs on SGLang (and vLLM),
        # reducing the chance of structured outputs being polluted by reasoning text.
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        if response_format is not None:
            payload["response_format"] = response_format

        # Merge extra_body in a deterministic order: defaults < per-call
        merged_extra = dict(self.default_extra_body)
        if extra_body:
            merged_extra.update(extra_body)
        if merged_extra:
            payload.update(merged_extra)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        t0 = time.perf_counter()
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout_s)
        dt = (time.perf_counter() - t0) * 1000.0

        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise LLMHTTPError(resp.status_code, "request failed", body=body)

        raw = resp.json()
        # OpenAI-like: choices[0].message.content
        # choices[0].message 이하에는 role, content 등이 있다.
        content = ""
        try:
            msg = raw["choices"][0].get("message") or {}
            content = msg.get("content") or ""
            if not content:
                # Some servers add reasoning_content; use it only as last resort.
                content = msg.get("reasoning_content") or ""
        except Exception:
            content = (raw.get("choices") or [{}])[0].get("text") or ""

        return ChatResult(content=str(content), raw=raw, latency_ms=float(dt))

    # -----------------------------
    # Helpers for constrained output
    # -----------------------------

    def choose_one(
        self,
        messages: list[dict[str, Any]],
        choices: list[str],
        *,
        temperature: float = 0.0,
        max_tokens: int = 32,
    ) -> ChatResult:
        """Return a single string that must be one of `choices`.

        Strategy by backend:
        - sglang: use regex constraint (extra_body={"regex": ...})
        - vllm:   use structured_outputs.choice then guided_choice
        - auto:   try vllm-style, then sglang-style, then unconstrained
        """
        if not choices:
            raise ValueError("choices must be non-empty")

        # --- SGLang path (regex) ---
        def _sglang_regex() -> ChatResult:
            # Anchor + optional whitespace to reduce accidental mismatches.
            inner = "|".join(re.escape(c) for c in choices)
            regex = rf"^\s*({inner})\s*$"
            return self.chat_completions(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"regex": regex},
            )

        # --- vLLM path (structured outputs / guided choice) ---
        def _vllm_choice() -> ChatResult:
            # vLLM >= 0.12.0: structured_outputs.choice
            return self.chat_completions(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"structured_outputs": {"choice": choices}},
            )

        def _vllm_guided_choice() -> ChatResult:
            # Older vLLM versions: guided_choice
            return self.chat_completions(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"guided_choice": choices},
            )

        # Dispatch
        if self.backend == "sglang":
            return _sglang_regex()

        if self.backend == "vllm":
            try:
                return _vllm_choice()
            except LLMHTTPError:
                try:
                    return _vllm_guided_choice()
                except LLMHTTPError:
                    return self.chat_completions(messages, temperature=temperature, max_tokens=max_tokens)

        # auto
        try:
            return _vllm_choice()
        except LLMHTTPError:
            pass
        try:
            return _vllm_guided_choice()
        except LLMHTTPError:
            pass
        try:
            return _sglang_regex()
        except LLMHTTPError:
            return self.chat_completions(messages, temperature=temperature, max_tokens=max_tokens)

    def generate_json(
        self,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        *,
        schema_name: str = "schema",
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> tuple[dict, ChatResult]:
        """Generate JSON matching `json_schema`.

        Returns (parsed_json, chat_result).
        
        * If using SGLang, return (LLM_message text, ChatResult),
        where ChatResult(content=str(content), raw=raw, latency_ms=float(dt))

        Strategy:
        - Prefer OpenAI-compatible structured outputs: response_format={"type":"json_schema", ...}
          (supported by SGLang structured outputs docs)
        - vLLM fallback (optional): structured_outputs.json / guided_json
        - Final fallback: unconstrained generation + best-effort JSON extraction
        """
        
        # json_schema=schema,
        # schema_name=f"query-prep-{settings.llm_prompt_version}",
        
        # Preferred: response_format={"type":"json_schema", "json_schema": {...}}
        try:
            res = self.chat_completions(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": json_schema},
                },
            )
            return _extract_first_json_obj(res.content), res
        except (LLMHTTPError, LLMParseError):
            pass

        # Optional vLLM-specific fallbacks (skip for sglang unless backend=auto)
        if self.backend in {"vllm", "auto"}:
            # vLLM >= 0.12.0: structured_outputs.json
            try:
                res = self.chat_completions(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body={"structured_outputs": {"json": json_schema}},
                )
                return _extract_first_json_obj(res.content), res
            except (LLMHTTPError, LLMParseError):
                pass

            # vLLM < 0.12.0: guided_json
            try:
                res = self.chat_completions(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body={"guided_json": json_schema},
                )
                return _extract_first_json_obj(res.content), res
            except (LLMHTTPError, LLMParseError):
                pass

        # Final fallback: unconstrained, extract JSON from text
        res = self.chat_completions(messages, temperature=temperature, max_tokens=max_tokens)
        return _extract_first_json_obj(res.content), res
