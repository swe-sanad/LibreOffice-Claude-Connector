# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Phase 1: core Claude API client.** [src/claude_client.py](../src/claude_client.py) —
  a zero-dependency (standard-library-only: `urllib` + `json` + `ssl`) client for
  Anthropic's Messages API, targeting Python 3.8+ for compatibility with LibreOffice's
  bundled interpreters (24.8 → 25.8). Includes a `ClaudeClient` class (`send()` with
  `prompt`/`messages`, `system`, `model`, `max_tokens`, `temperature`), a typed error
  hierarchy (`ClaudeError` → `ClaudeConfigError` / `ClaudeAuthError` /
  `ClaudeRateLimitError` / `ClaudeAPIError` / `ClaudeNetworkError`), retries with
  exponential backoff honoring `retry-after`, a `ClaudeResult` dataclass, and
  `extract_text()` for joining multi-block text responses.
- [tests/test_claude_client.py](../tests/test_claude_client.py) — 14 offline unit tests
  covering the client, mocking `urllib` so no API key or network is required.
- [scripts/spike_http.py](../scripts/spike_http.py) — a live smoke test run with
  LibreOffice's bundled Python, requiring `ANTHROPIC_API_KEY`.
- Verified: all 14 offline tests pass on LibreOffice's bundled Python 3.10.17
  (LibreOffice 25.2.3.2, Windows). A live request from the bundled interpreter to
  `api.anthropic.com/v1/messages` correctly returned HTTP 401 (`invalid x-api-key`),
  mapped to `ClaudeAuthError` — confirming TLS, reachability, headers, and error
  parsing all work out of the box.
- `LICENSE` (MPL-2.0) and `.gitignore` (excludes secrets/keys) added.
