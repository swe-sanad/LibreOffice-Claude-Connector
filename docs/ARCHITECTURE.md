# Architecture

This document describes the technical design of the connector as it stands after
**Phase 2**. For the underlying research this design is based on (LibreOffice's UNO
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
UNO I/O layer             — src/uno_bridge.py (THIS PHASE, Calc only; Writer lands in Phase 3)
        │
        ▼
Pure action logic         — src/calc_actions.py (THIS PHASE)
        │
        ▼
Pure Claude client        — src/claude_client.py (Phase 1)
```

**The bottom three layers exist today; only the extension UI layer (Phase 4) remains.**
[src/claude_client.py](../src/claude_client.py) is deliberately pure: it takes a
prompt/messages in, performs one blocking HTTPS call, and returns a typed result — it
has no knowledge of UNO, threads, or documents. This keeps it independently testable
(see [tests/test_claude_client.py](../tests/test_claude_client.py)) and reusable from
both Calc and Writer integrations.

## Calc transform: pure logic vs. UNO I/O (Phase 2)

Phase 2 splits the Calc "rewrite the selection" feature into two modules with a hard
boundary, mirroring the Claude-client split above:

- **[src/calc_actions.py](../src/calc_actions.py) — pure, UNO-free, network-free.** It
  never imports `uno` and never touches the network directly; it only builds prompts,
  parses Claude's reply, and coerces cell values. This means the entire transform logic
  — including the tricky "does the model's reply actually parse into the right shape"
  path — is unit-testable on any machine with plain Python 3.8+, no LibreOffice install
  and no API key required (see [tests/test_calc_actions.py](../tests/test_calc_actions.py),
  17 tests).
- **[src/uno_bridge.py](../src/uno_bridge.py) — UNO glue.** It imports `uno` and is the
  only module that touches `XCellRange`/`getDataArray`/`setDataArray`. It calls into
  `calc_actions` for the pure transform and cell-coercion logic, but never the reverse —
  `calc_actions` has zero knowledge that UNO exists.

### The grid contract

The interface between "what's in the spreadsheet" and "what Claude returns" is a strict
same-shape contract, enforced entirely in `calc_actions.parse_grid`:

- Claude is instructed (via `build_system_prompt`) to reply with **only** a JSON object
  `{"cells": [[...], [...]]}` — no markdown fences, no prose — whose outer array has
  exactly `nrows` rows and every inner array has exactly `ncols` columns, matching the
  input selection's dimensions exactly.
- `parse_grid` is tolerant of a model that ignores the "no markdown" instruction anyway:
  it strips ```` ```json ```` fences and extracts the outermost `{...}`/`[...]` span
  before calling `json.loads`, so stray prose around the JSON doesn't break parsing.
- Any dimension mismatch (wrong row count, wrong column count in any row, missing
  `"cells"` key, invalid JSON) raises `TransformError` with an actionable message rather
  than silently truncating or padding the grid.
- Every surviving cell is passed through `coerce_out_cell` so the result is always
  something `setDataArray` will accept: `None`/JSON `null` → `""`, `bool` → `"TRUE"`/`"FALSE"`,
  `int`/`float` → `float`, anything else → `str`.

### Selection normalization

LibreOffice's `doc.getCurrentSelection()` can return three different shapes depending on
what the user has selected in Calc: a single `SheetCell`, a rectangular `SheetCellRange`,
or a `SheetCellRanges` (multiple disjoint ranges, e.g. a ctrl-click selection).
`uno_bridge.get_calc_selection_range(doc)` normalizes all three down to a single
`XCellRange` before any read/write happens, so the rest of the pipeline (`calc_actions`,
`read_range_grid`/`write_range_grid`) only ever deals with one rectangular range:

- `SheetCell` → wrapped into a 1×1 `XCellRange` at that cell's address.
- `SheetCellRange` → returned as-is.
- `SheetCellRanges` → the **first** contained range is used (multi-range selections
  beyond the first range are not yet supported).
- Anything else selected (a shape, a chart, ...) → `None`, and the caller
  (`transform_selection`) raises a `RuntimeError` asking the user to select cells first.

## Integration testing approach

Because `uno_bridge.py` imports `uno`, it cannot be unit-tested with a mocked/faked UNO
— it is tested against a **real, running LibreOffice** instead. Rather than requiring a
developer's own LibreOffice window to be sacrificed for testing,
[scripts/run_integration.ps1](../scripts/run_integration.ps1) launches an **isolated**
headless `soffice` instance (its own `-env:UserInstallation` profile directory and its
own UNO socket port), waits for that socket to accept connections, runs a given
integration test script against it (default:
[tests/integration/test_calc_uno.py](../tests/integration/test_calc_uno.py)), and then
terminates that instance — leaving the developer's own open LibreOffice, if any,
untouched. The Calc integration test uses a deterministic stub transform (uppercase
text, `+1` to numbers) in place of a real Claude call, so it needs no
`ANTHROPIC_API_KEY` and exercises only the UNO read/selection-normalize/write path.

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

## Threading model (still planned — not yet implemented)

`ClaudeClient.send()` is **intentionally pure and synchronous**: it blocks on one
HTTPS request. That is correct in isolation, but calling it directly from LibreOffice's
UI thread would freeze Calc/Writer for the duration of the request. The locked design
(see [BUILD-PLAN.md](BUILD-PLAN.md)) is:

1. The UNO/extension layer reads the user's selection on the main thread.
2. It hands the request off to `ClaudeClient.send()` on a **worker thread**.
3. The result (or a `ClaudeError`) is marshalled back to the **main thread** before any
   document mutation (`setDataArray` / `insertString`) is performed — UNO document APIs
   are not safe to call from arbitrary threads.

Phase 2's `uno_bridge.transform_selection()` is currently **synchronous end to end**
(read → call Claude → write, all on the calling thread) — it is suitable for a
menu-triggered macro today, but does not yet offload the network call to a worker
thread. The worker-thread/marshalling split described above still lands with the
packaged extension in Phase 4.

## Cross-version Python target

Per the locked decision in [BUILD-PLAN.md](BUILD-PLAN.md) ("Target Python 3.8/3.9
stdlib only"), the client avoids syntax and stdlib features newer than Python 3.8, so
the same source runs unmodified across the Python versions bundled with LibreOffice
24.8 → 25.8 (3.9 → 3.11). This has been verified in Phase 1 against LibreOffice
25.2.3.2's bundled Python 3.10.17 — see [DEVELOPMENT.md](DEVELOPMENT.md) for how to
reproduce that verification.
