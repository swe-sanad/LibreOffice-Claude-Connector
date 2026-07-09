# Architecture

This document describes the technical design of the connector as it stands after
**Phase 1**. For the underlying research this design is based on (LibreOffice's UNO
API, the bundled-Python environment, the Claude Messages API, and prior-art review),
see [RESEARCH.md](RESEARCH.md). For the phase-by-phase roadmap, see
[BUILD-PLAN.md](BUILD-PLAN.md).

## Layered design

The connector is built as four layers, each with a single responsibility, so the
Claude-calling logic never needs to know it is running inside LibreOffice:

```
LibreOffice (Calc / Writer)
        │  UNO API (getCurrentSelection, getDataArray/setDataArray, insertString)
        ▼
Extension UI layer        — menu/toolbar/shortcut → ProtocolHandler → XDispatch (Phase 4)
        │
        ▼
UNO I/O layer             — reads the selection, writes results back on the main thread (Phase 2/3)
        │
        ▼
Pure action logic         — builds the prompt/instruction, shapes request/response (Phase 2/3)
        │
        ▼
Pure Claude client        — src/claude_client.py (THIS PHASE)
```

**Only the bottom layer exists today.** [src/claude_client.py](../src/claude_client.py)
is deliberately pure: it takes a prompt/messages in, performs one blocking HTTPS call,
and returns a typed result — it has no knowledge of UNO, threads, or documents. This
keeps it independently testable (see [tests/test_claude_client.py](../tests/test_claude_client.py))
and reusable from both Calc and Writer integrations later.

## The Claude client (`src/claude_client.py`)

- **Zero dependencies**: `urllib.request` + `json` + `ssl` only — no `requests`, no
  `anthropic` SDK. This is a hard requirement because LibreOffice's bundled Python has
  no `pip` and no third-party packages.
- **`ClaudeClient`**: constructed with an API key (and optional model/base URL/timeout/
  retry settings). Its `send(prompt=... | messages=..., system=..., model=..., max_tokens=..., temperature=...)`
  method issues one Messages API request and returns a `ClaudeResult`.
- **`ClaudeResult`**: a dataclass exposing `text`, `stop_reason`, `model`, `usage`,
  `id`, the `raw` response, and derived properties `truncated` (`stop_reason == "max_tokens"`),
  `input_tokens`, `output_tokens`.
- **`extract_text()`**: joins every `type == "text"` content block in a Messages API
  response — the API returns `content` as an array of blocks, so a plain answer can
  legitimately span more than one text block.
- **Retries**: transient statuses (408/409/429/500/502/503/529) are retried with
  exponential backoff up to `max_retries`, honoring the server's `retry-after` header
  when present.

### Error hierarchy

All errors derive from `ClaudeError`, so callers can catch broadly or narrowly:

```
ClaudeError
├── ClaudeConfigError     invalid/missing configuration (e.g. no API key)
├── ClaudeAuthError       HTTP 401/403
├── ClaudeRateLimitError  HTTP 429 after retries are exhausted (carries retry_after)
├── ClaudeAPIError        any other non-success HTTP response (carries status + error_type)
└── ClaudeNetworkError    DNS/connection/TLS/timeout — no HTTP response was received
```

## Threading model (planned, not yet implemented)

`ClaudeClient.send()` is **intentionally pure and synchronous**: it blocks on one
HTTPS request. That is correct in isolation, but calling it directly from LibreOffice's
UI thread would freeze Calc/Writer for the duration of the request. The locked design
(see [BUILD-PLAN.md](BUILD-PLAN.md), Phase 2/3) is:

1. The UNO/extension layer reads the user's selection on the main thread.
2. It hands the request off to `ClaudeClient.send()` on a **worker thread**.
3. The result (or a `ClaudeError`) is marshalled back to the **main thread** before any
   document mutation (`setDataArray` / `insertString`) is performed — UNO document APIs
   are not safe to call from arbitrary threads.

This threading/marshalling code does not exist yet; it lands in Phase 2 alongside the
first Calc integration.

## Cross-version Python target

Per the locked decision in [BUILD-PLAN.md](BUILD-PLAN.md) ("Target Python 3.8/3.9
stdlib only"), the client avoids syntax and stdlib features newer than Python 3.8, so
the same source runs unmodified across the Python versions bundled with LibreOffice
24.8 → 25.8 (3.9 → 3.11). This has been verified in Phase 1 against LibreOffice
25.2.3.2's bundled Python 3.10.17 — see [DEVELOPMENT.md](DEVELOPMENT.md) for how to
reproduce that verification.
