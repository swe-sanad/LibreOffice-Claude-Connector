# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Non-Anthropic LLM providers, standard-library only.

One class: :class:`OpenAICompatibleClient`, which speaks the OpenAI
``/chat/completions`` shape and therefore covers **Ollama, LM Studio,
OpenRouter, Together, OpenAI**, and any local/cloud OpenAI-compatible endpoint.

It honours the exact same ``send(...)`` contract as :class:`claude_client.ClaudeClient`
and returns a :class:`claude_client.ClaudeResult`, so the rest of the app (the
pure ``calc_actions``/``writer_actions`` layer) needs zero changes — the client
is just dependency-injected. The retry/HTTP/error transport is shared via
``claude_client._post_json``; only the request/response *shape* differs.

# ponytail: no Provider base class / ABC — the duck-typed send() IS the contract
# the whole codebase already relies on. Add an ABC only if a third shape appears.
"""

from __future__ import annotations

import ssl
import time
from typing import Any, Dict, Optional, Sequence, Union

try:                                  # packaged in the .oxt (claudeconn package)
    from .claude_client import (
        DEFAULT_MAX_TOKENS, DEFAULT_MAX_RETRIES, DEFAULT_MODEL, DEFAULT_TIMEOUT,
        ClaudeConfigError, ClaudeResult, _post_json, _require_https,
    )
except ImportError:                   # flat layout (tests / dev)
    from claude_client import (
        DEFAULT_MAX_TOKENS, DEFAULT_MAX_RETRIES, DEFAULT_MODEL, DEFAULT_TIMEOUT,
        ClaudeConfigError, ClaudeResult, _post_json, _require_https,
    )

# Sensible local default; overridden via settings (base_url).
DEFAULT_OPENAI_BASE_URL = "http://localhost:11434/v1"   # Ollama


class OpenAICompatibleClient:
    """LLM client for any OpenAI-compatible ``/chat/completions`` endpoint.

    ``api_key`` may be empty for a local server (Ollama/LM Studio need none);
    when set it is sent as a ``Bearer`` token. ``base_url`` is the API root
    (e.g. ``http://localhost:11434/v1`` or ``https://openrouter.ai/api/v1``);
    ``/chat/completions`` is appended.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep=time.sleep,
    ) -> None:
        if timeout is None or timeout <= 0:
            raise ClaudeConfigError("timeout must be a positive number of seconds.")
        if max_retries < 0:
            raise ClaudeConfigError("max_retries must be >= 0.")
        _require_https(base_url)              # allows plain http only for localhost
        self.api_key = (api_key or "").strip()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._sleep = sleep
        self._ssl_context = ssl.create_default_context()

    def send(
        self,
        prompt: Optional[str] = None,
        *,
        messages: Optional[Sequence[Dict[str, Any]]] = None,
        system: Optional[Union[str, Sequence[Dict[str, Any]]]] = None,
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: Optional[float] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> ClaudeResult:
        if (prompt is None) == (messages is None):
            raise ClaudeConfigError("Provide exactly one of `prompt` or `messages`.")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ClaudeConfigError("max_tokens must be a positive integer.")

        msgs = list(messages) if messages is not None else [{"role": "user", "content": prompt}]
        if system:
            # OpenAI shape: system is a leading role:"system" message (not top-level).
            msgs = [{"role": "system", "content": system}] + msgs

        body: Dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if extra_body:
            body.update(extra_body)

        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = "Bearer " + self.api_key

        payload = _post_json(
            self.base_url + "/chat/completions", headers, body,
            ssl_context=self._ssl_context, timeout=self.timeout,
            max_retries=self.max_retries, sleep=self._sleep)

        choice = (payload.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        return ClaudeResult(
            text=(choice.get("message") or {}).get("content") or "",
            # map OpenAI's "length" onto Anthropic's "max_tokens" so .truncated works
            stop_reason="max_tokens" if finish == "length" else finish,
            model=payload.get("model"),
            usage=payload.get("usage") or {},
            id=payload.get("id"),
            raw=payload,
        )
