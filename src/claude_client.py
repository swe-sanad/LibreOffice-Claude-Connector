# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Anthropic Claude Messages API client — standard library only.

This module deliberately depends on **nothing** outside the Python standard
library (``urllib`` + ``json`` + ``ssl``). That is a hard requirement: it runs
inside LibreOffice's *bundled* Python interpreter, which ships no ``pip`` and no
third-party packages (no ``requests``, no ``anthropic`` SDK).

It is written to be compatible with **Python 3.8+** so the same code runs across
the LibreOffice versions users actually have:

    LibreOffice 24.8  -> Python 3.9
    LibreOffice 25.2  -> Python 3.10
    LibreOffice 25.8  -> Python 3.11

The client is intentionally *pure* and *synchronous*: it performs one blocking
HTTPS request and returns a result. Threading (so LibreOffice's UI does not
freeze) is the responsibility of the caller — see ``connector`` / the UNO layer,
which runs :meth:`ClaudeClient.send` on a worker thread and marshals the result
back to the main thread before touching the document.

Reference: https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_ANTHROPIC_VERSION",
    "DEFAULT_MODEL",
    "ClaudeError",
    "ClaudeConfigError",
    "ClaudeResult",
    "ClaudeClient",
]

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
# Dateless IDs are pinned snapshots (Claude 4.6 gen and later): safe to pin.
# Kept configurable everywhere; this is only the fallback default.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 3

# HTTP statuses that are worth retrying (transient / rate-limited / overloaded).
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 529})
_MAX_BACKOFF_SECONDS = 30.0
# A server-supplied Retry-After is honored up to this; our own exponential
# backoff stays capped at _MAX_BACKOFF_SECONDS.
_MAX_RETRY_AFTER = 120.0
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

# ponytail: one error type + a config-error subclass the caller catches distinctly.
# Auth/rate-limit/api/network all raise ClaudeError with a specific message (the
# only thing any caller reads). Add typed subclasses back when a caller actually
# branches on the error kind — nothing does today.
class ClaudeError(Exception):
    """Base class for every error raised by this module."""


class ClaudeConfigError(ClaudeError):
    """The client was called with invalid/missing configuration (e.g. no key)."""


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #

@dataclass
class ClaudeResult:
    """A parsed, convenient view over a Messages API response."""

    text: str
    stop_reason: Optional[str]
    model: Optional[str]
    usage: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """True when the answer was cut off by ``max_tokens``."""
        return self.stop_reason == "max_tokens"

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("input_tokens", 0) or 0)

    @property
    def output_tokens(self) -> int:
        return int(self.usage.get("output_tokens", 0) or 0)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def extract_text(payload: Dict[str, Any]) -> str:
    """Concatenate the text of every ``type == "text"`` block in a response.

    The Messages API returns ``content`` as an *array of blocks*; a plain answer
    can span several text blocks, and tool-use / thinking produce other block
    types. Never assume ``content[0].text``.
    """
    parts: List[str] = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _require_https(url: str) -> None:
    """Reject a base URL that would send the API key in cleartext.

    HTTPS is required; plain http is allowed only for a local proxy so the key
    never leaves the machine unencrypted.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in _LOCAL_HOSTS:
        raise ClaudeConfigError(
            "Refusing to send the API key over a non-HTTPS URL (%s). Use https:// "
            "(plain http is allowed only for localhost)." % url)


def _coerce_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _backoff_delay(attempt: int, retry_after: Optional[float]) -> float:
    if retry_after is not None:
        return min(retry_after, _MAX_RETRY_AFTER)
    return min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)


def _post_json(url, headers, body, *, ssl_context, timeout, max_retries, sleep):
    """POST a JSON body with retries; return the parsed response dict.

    Shared transport for every provider client (Anthropic + OpenAI-compatible):
    same stdlib urllib call, retry policy, and error->ClaudeError mapping. Messages
    are provider-neutral so both clients reuse them verbatim.
    """
    data = json.dumps(body).encode("utf-8")
    attempt = 0
    while True:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))

        except urllib.error.HTTPError as exc:
            status = exc.code
            message = _read_http_error(exc)
            retry_after = _coerce_retry_after(exc.headers.get("retry-after"))
            if status in _RETRYABLE_STATUS and attempt < max_retries:
                sleep(_backoff_delay(attempt + 1, retry_after))
                attempt += 1
                continue
            if status in (401, 403):
                raise ClaudeError("Authentication failed (HTTP %s): %s" % (status, message)) from exc
            if status == 429:
                raise ClaudeError("Rate limited (HTTP 429): %s" % message) from exc
            raise ClaudeError("API error (HTTP %s): %s" % (status, message)) from exc

        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt < max_retries:
                sleep(_backoff_delay(attempt + 1, None))
                attempt += 1
                continue
            reason = getattr(exc, "reason", exc)
            raise ClaudeError("Network/TLS error: %s" % (reason,)) from exc


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class ClaudeClient:
    """A thin, robust, dependency-free wrapper over the Messages API.

    Parameters
    ----------
    api_key:
        Anthropic API key (``sk-ant-...``). Required and non-empty.
    model:
        Default model id. Overridable per :meth:`send` call.
    base_url:
        Messages endpoint. Overridable for proxies / gateways.
    anthropic_version:
        Value for the ``anthropic-version`` header.
    timeout:
        Per-request socket timeout in seconds. Never ``None`` (an infinite
        timeout would hang LibreOffice forever).
    max_retries:
        Number of *additional* attempts on transient failures.
    sleep:
        Injected sleep function (defaults to :func:`time.sleep`); overridden in
        tests so retries do not actually block.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not str(api_key).strip():
            raise ClaudeConfigError("Anthropic API key is missing or empty.")
        if timeout is None or timeout <= 0:
            raise ClaudeConfigError("timeout must be a positive number of seconds.")
        if max_retries < 0:
            raise ClaudeConfigError("max_retries must be >= 0.")

        _require_https(base_url)
        self.api_key = str(api_key).strip()
        self.model = model
        self.base_url = base_url
        self.anthropic_version = anthropic_version
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._sleep = sleep
        # Build the TLS context once from the OS trust store; api.anthropic.com
        # chains to a public root, so this "just works".
        # ponytail: system CA store only; add a ca_file arg if a TLS-inspecting
        # proxy ever needs a custom bundle.
        self._ssl_context = ssl.create_default_context()

    # -- public API -------------------------------------------------------- #

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
        """Send a single (multi-turn capable) request and return a result.

        Pass **either** ``prompt`` (a single user turn) **or** ``messages`` (a
        full role/content array). ``system`` is the top-level system prompt (NOT
        a ``role: "system"`` message — that is the Anthropic shape).
        """
        if (prompt is None) == (messages is None):
            raise ClaudeConfigError("Provide exactly one of `prompt` or `messages`.")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ClaudeConfigError("max_tokens must be a positive integer.")

        if messages is None:
            messages = [{"role": "user", "content": prompt}]

        body: Dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": list(messages),
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        if extra_body:
            body.update(extra_body)

        payload = self._request(body)

        return ClaudeResult(
            text=extract_text(payload),
            stop_reason=payload.get("stop_reason"),
            model=payload.get("model"),
            usage=payload.get("usage") or {},
            id=payload.get("id"),
            raw=payload,
        )

    # -- internals --------------------------------------------------------- #

    def _headers(self) -> Dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
        }

    def _request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return _post_json(
            self.base_url, self._headers(), body,
            ssl_context=self._ssl_context, timeout=self.timeout,
            max_retries=self.max_retries, sleep=self._sleep)


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    """Best-effort parse of an Anthropic error body -> a human message."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - defensive; never mask the original error
        return str(exc)
    try:
        parsed = json.loads(body)
        return (parsed.get("error") or {}).get("message") or body
    except (ValueError, AttributeError):
        return body or str(exc)
