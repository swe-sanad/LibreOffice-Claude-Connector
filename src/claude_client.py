# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
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
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_ANTHROPIC_VERSION",
    "DEFAULT_MODEL",
    "ClaudeError",
    "ClaudeConfigError",
    "ClaudeAuthError",
    "ClaudeRateLimitError",
    "ClaudeAPIError",
    "ClaudeNetworkError",
    "ClaudeResult",
    "ClaudeClient",
    "extract_text",
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


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class ClaudeError(Exception):
    """Base class for every error raised by this module."""


class ClaudeConfigError(ClaudeError):
    """The client was called with invalid/missing configuration (e.g. no key)."""


class ClaudeAuthError(ClaudeError):
    """Authentication/permission failure (HTTP 401 / 403)."""


class ClaudeRateLimitError(ClaudeError):
    """Rate limited (HTTP 429) and retries were exhausted.

    ``retry_after`` is the server-suggested wait in seconds, if provided.
    """

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ClaudeAPIError(ClaudeError):
    """A non-success HTTP response that is not auth/rate-limit.

    Carries the HTTP ``status`` and the Anthropic ``error_type`` when available.
    """

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        error_type: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class ClaudeNetworkError(ClaudeError):
    """DNS / connection / TLS / timeout failure — no HTTP response was received."""


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


def _coerce_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


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
    ca_file:
        Optional path to a CA bundle (e.g. a vendored ``certifi`` ``cacert.pem``)
        for environments whose Python cannot see the OS trust store or that sit
        behind a TLS-inspecting proxy. ``None`` uses the system trust store.
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
        ca_file: Optional[str] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not str(api_key).strip():
            raise ClaudeConfigError("Anthropic API key is missing or empty.")
        if timeout is None or timeout <= 0:
            raise ClaudeConfigError("timeout must be a positive number of seconds.")
        if max_retries < 0:
            raise ClaudeConfigError("max_retries must be >= 0.")

        self.api_key = str(api_key).strip()
        self.model = model
        self.base_url = base_url
        self.anthropic_version = anthropic_version
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._sleep = sleep
        # Build the TLS context once. On Windows this loads the OS trust store;
        # api.anthropic.com chains to a public root, so this "just works".
        if ca_file:
            self._ssl_context = ssl.create_default_context(cafile=ca_file)
        else:
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
        stop_sequences: Optional[Sequence[str]] = None,
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
        if stop_sequences:
            body["stop_sequences"] = list(stop_sequences)
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

    def _backoff_delay(self, attempt: int, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            return min(retry_after, _MAX_BACKOFF_SECONDS)
        return min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)

    def _request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        attempt = 0
        while True:
            request = urllib.request.Request(
                self.base_url, data=data, headers=self._headers(), method="POST"
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._ssl_context
                ) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return json.loads(response.read().decode(charset))

            except urllib.error.HTTPError as exc:
                status = exc.code
                error_type, message = _read_http_error(exc)
                retry_after = _coerce_retry_after(exc.headers.get("retry-after"))

                if status in _RETRYABLE_STATUS and attempt < self.max_retries:
                    self._sleep(self._backoff_delay(attempt + 1, retry_after))
                    attempt += 1
                    continue

                if status in (401, 403):
                    raise ClaudeAuthError(
                        "Authentication failed (HTTP %s): %s" % (status, message)
                    ) from exc
                if status == 429:
                    raise ClaudeRateLimitError(
                        "Rate limited (HTTP 429): %s" % message, retry_after
                    ) from exc
                raise ClaudeAPIError(
                    "Claude API error (HTTP %s): %s" % (status, message),
                    status=status,
                    error_type=error_type,
                ) from exc

            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                if attempt < self.max_retries:
                    self._sleep(self._backoff_delay(attempt + 1, None))
                    attempt += 1
                    continue
                reason = getattr(exc, "reason", exc)
                raise ClaudeNetworkError(
                    "Network/TLS error contacting Claude API: %s" % (reason,)
                ) from exc


def _read_http_error(exc: urllib.error.HTTPError) -> "tuple[Optional[str], str]":
    """Best-effort parse of an Anthropic error body -> (error_type, message)."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - defensive; never mask the original error
        return None, str(exc)
    try:
        parsed = json.loads(body)
        err = parsed.get("error") or {}
        return err.get("type"), err.get("message") or body
    except (ValueError, AttributeError):
        return None, body or str(exc)
