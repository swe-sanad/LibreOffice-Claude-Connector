# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Phase 2: Calc rewrite-selection (transform logic + UNO bridge).**
  [src/calc_actions.py](../src/calc_actions.py) — pure, UNO-free, network-free transform
  logic: `build_system_prompt`/`build_user_prompt`, a tolerant `parse_grid(text, nrows, ncols)`
  that survives markdown fences and surrounding prose while enforcing the "grid contract"
  (Claude must return `{"cells": [[...]]}` with the exact same row/column shape as the
  input), `coerce_out_cell` (`None`/JSON `null` → `""`, `bool` → `"TRUE"`/`"FALSE"`,
  int/float → `float`, else `str`), and `transform_range(client, data, instruction, ...)`
  orchestrating build → send → parse end to end.
- [src/uno_bridge.py](../src/uno_bridge.py) — the UNO glue: `connect()` (socket resolve
  for dev/test), `get_calc_selection_range(doc)` normalizing `SheetCell` /
  `SheetCellRange` / `SheetCellRanges` selections to one `XCellRange`,
  `read_range_grid`/`write_range_grid` (`getDataArray`/`setDataArray`, with defensive
  `None`-coercion at the write boundary), and a synchronous `transform_selection(doc,
  client, instruction)` tying read → transform → write together.
- [tests/test_calc_actions.py](../tests/test_calc_actions.py) — 17 offline unit tests for
  `calc_actions` (no UNO, no network, no key required).
- [tests/integration/test_calc_uno.py](../tests/integration/test_calc_uno.py) — a LIVE
  integration test that drives a real headless LibreOffice over UNO: reads a 2×2 range
  selection, writes a transformed grid back in one `setDataArray` call, normalizes a
  single-cell selection to a 1×1 range, and coerces `None` → `""` on write. Uses a
  deterministic stub transform, so it needs no `ANTHROPIC_API_KEY`.
- [scripts/run_integration.ps1](../scripts/run_integration.ps1) — launches an ISOLATED
  headless LibreOffice (its own user profile, does not disturb the developer's open
  office), waits for the UNO socket, runs a given integration test script, then
  terminates that instance.
- Verified: the full offline suite is now **31 tests, all passing** on LibreOffice's
  bundled Python 3.10.17. The Calc UNO integration test **passed against real
  LibreOffice 25.2.3.2**, confirming the read/transform/write path works end to end
  against the real application. (The live Claude network call itself was already proven
  in Phase 1's TLS/401 assertion; it slots into `transform_range` once
  `ANTHROPIC_API_KEY` is set.)

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
