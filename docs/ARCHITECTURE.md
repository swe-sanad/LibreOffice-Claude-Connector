# Architecture

This document describes the technical design of the connector as it stands after
**Phase 4-5**. For the underlying research this design is based on (LibreOffice's UNO
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
Extension UI layer        — menu/toolbar/shortcut → ProtocolHandler → XDispatch (Phase 4-5)
        │
        ▼
UNO I/O layer             — src/uno_bridge.py (Calc + Writer sections)
        │
        ▼
Pure action logic         — src/calc_actions.py (Phase 2) / src/writer_actions.py (Phase 3)
        │
        ▼
Pure Claude client        — src/claude_client.py (Phase 1)
```

**All four layers are implemented today; Phase 4-5 packages the top layer as a real,
installable `.oxt` extension.** [src/claude_client.py](../src/claude_client.py) is
deliberately pure: it takes a
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

## Writer rewrite-selection / generate-at-caret (Phase 3)

Phase 3 mirrors the Calc split for Writer, with the same hard boundary between pure
logic and UNO glue:

- **[src/writer_actions.py](../src/writer_actions.py) — pure, UNO-free, network-free.**
  Writer output is plain text (not a grid), so this is simpler than the Calc side: build
  a prompt, send, clean the reply. `clean_output` unwraps a whole-output markdown code
  fence but deliberately does **not** strip surrounding quotes or inline backticks — a
  legitimately quoted rewrite would otherwise be damaged. `default_max_tokens(text)`
  scales the output budget to the input length, bounded to `[512, 8192]`, so a short
  rewrite doesn't reserve 8192 tokens and a long one isn't cut off at a fixed 1024.
- **[src/uno_bridge.py](../src/uno_bridge.py) (Writer section)** — imports `uno` and is
  the only code that touches the view cursor / `XText`. It calls into `writer_actions`
  for the pure prompt/clean logic but never the reverse.

### Rewrite vs. generate

`rewrite_writer_selection(doc, client, instruction)` inspects the current view cursor
via `get_writer_selection(doc)`, which reads `isCollapsed()` — the reliable signal for
"nothing is selected, just a caret position":

- **Has a selection** (`isCollapsed() == False`) → `writer_actions.rewrite_text` sends
  the selected text + instruction to Claude, and `replace_writer_selection` overwrites
  the selection with the cleaned reply.
- **No selection** (caret only) → `writer_actions.generate_text` sends just the
  instruction, and `insert_writer_at_caret` inserts the cleaned reply at the caret
  without touching surrounding text.

Both paths are synchronous end to end (read → call Claude → write), same as
`transform_selection` in Calc; the worker-thread split described below still lands in
Phase 4.

### Multi-paragraph handling

Claude's replies are plain text with `\n` separating what should become separate
paragraphs, but UNO documents don't understand `\n` — a paragraph break is a distinct
`PARAGRAPH_BREAK` control character. `uno_bridge._insert_multiline(xtext, xrange, text,
absorb)` splits the reply on `\n` and inserts the first line via `xtext.insertString`
(`absorb=True` replaces the range's content for a rewrite, `absorb=False` inserts at a
caret without replacing), then for every remaining line, inserts a real
`insertControlCharacter(..., PARAGRAPH_BREAK, ...)` followed by `insertString`. Mutations
are grouped into one named undo step by `_with_undo` (via `doc.getUndoManager()`), so a
multi-paragraph rewrite or insert undoes in a single Ctrl+Z.

**Cursor-collapse gotcha:** after each `insertString`/`insertControlCharacter` call, the
cursor still *spans* the just-inserted text rather than collapsing to its end. Without
an explicit `cursor.collapseToEnd()` after every insert, each subsequent insert lands at
the *start* of the previous span and the paragraphs come out reversed/garbled. This is
handled in `_insert_multiline` but was only caught by the real-LibreOffice integration
test — see [DEVELOPMENT.md](DEVELOPMENT.md#gotchas) for the full story on why this class
of bug is invisible to mocked/offline tests.

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
[tests/integration/test_writer_uno.py](../tests/integration/test_writer_uno.py) does the
same for Writer: it drives selection read/replace, confirms a `\n` in the replacement
becomes a real paragraph break, and exercises caret-detection + insert-at-caret. The
runner script now pre-kills any stale test instance (matched by a unique profile
marker, so a normal LibreOffice window is never touched) and uses a 150s cold-start
budget.

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

## The extension layer (`src/connector.py`, Phase 4-5)

[src/connector.py](../src/connector.py) is the **single registered UNO component** in
the packaged extension: a `com.sun.star.frame.ProtocolHandler` implementing
`XDispatchProvider` + `XDispatch` + `XInitialization` + `XServiceInfo`. It owns two
command URLs, wired from the menu/toolbar via [ext/Addons.xcu](../ext/Addons.xcu) and
registered against the protocol in [ext/ProtocolHandler.xcu](../ext/ProtocolHandler.xcu):

- `com.swepioneers.claudeconnector:Transform` — transform the Calc selection, or
  rewrite/generate the Writer selection/caret.
- `com.swepioneers.claudeconnector:Settings` — open the settings dialog.

`queryDispatch` claims any URL under its own protocol; `dispatch()` looks at the
document type (`uno_bridge.is_calc`/`is_writer`) and routes to the matching Calc or
Writer path. Any `ClaudeError`/`ClaudeConfigError`/unexpected exception is caught at
the top of `dispatch()` and turned into an AWT error message box — no exception is
ever allowed to escape back into UNO.

### Threading model

`ClaudeClient.send()` is **intentionally pure and synchronous**: it blocks on one
HTTPS request. Calling it directly from LibreOffice's UI thread would freeze Calc/
Writer for the duration of the request, so `connector.py` splits each command into
three steps:

1. **Main thread** — read the user's selection (`uno_bridge.get_calc_selection_range`/
   `read_range_grid`, or `uno_bridge.get_writer_selection`).
2. **Worker thread** — [src/uno_ui.py](../src/uno_ui.py)'s `run_with_progress(ctx,
   win, title, message, work)` spawns a daemon thread running `work()` (which calls
   `calc_actions.transform_range`/`writer_actions.rewrite_text`/`generate_text`, and
   therefore `ClaudeClient.send()`) while a modal AWT progress dialog keeps the UI
   responsive. The dialog's nested event loop (`dialog.execute()`) blocks the caller
   until the worker signals completion.
3. **Back on the main thread** — the worker thread cannot safely call UNO/AWT itself,
   so on completion it hands a `com.sun.star.awt.AsyncCallback` an `_EndDialogCallback`
   that calls `dialog.endExecute()`; that callback is guaranteed to run on the main
   thread. Once `run_with_progress` returns, `connector.py` performs the document
   mutation (`uno_bridge.write_range_grid` / `replace_writer_selection` /
   `insert_writer_at_caret`) back on the main thread — UNO document APIs are not safe
   to call from arbitrary threads.

If `parent_win` is `None` (headless), `run_with_progress` just calls `work()`
synchronously — this is what the offline/dev paths and the extension-dispatch
integration test rely on.

### UI (`src/uno_ui.py`)

Built entirely from `com.sun.star.awt.UnoControlDialogModel` controls (no `.xdl`
files): `info_box`/`error_box` (message boxes), `prompt_instruction` (a modal
multi-line instruction prompt used by both Transform paths), and `settings_dialog`
(model dropdown + masked API-key field; a blank key field means "keep the existing
key"). All of it requires a live UNO frame, so it is only exercised inside LibreOffice,
not the offline unit tests.

### Packaging (`ext/`, `scripts/build_oxt.py`)

The `.oxt` is a ZIP assembled by [scripts/build_oxt.py](../scripts/build_oxt.py) from
`ext/` (metadata: `description.xml`, `META-INF/manifest.xml`, `Addons.xcu`,
`ProtocolHandler.xcu`, `description/desc_en.txt`, generated `icons/*.png` from
`scripts/make_icons.py`) plus `src/`. `connector.py` is placed at the archive root
(the one registered component); every other helper module (`claude_client`,
`calc_actions`, `writer_actions`, `uno_bridge`, `config`, `keystore`, `uno_ui`) is
copied under `pythonpath/claudeconn/` as an importable package — this avoids any of
those generic module names colliding with another extension's top-level modules.
`connector.py` adds both `pythonpath/` and its own directory to `sys.path` and tries
the packaged import (`from claudeconn import ...`) first, falling back to a flat
import for local/dev runs where the helpers sit next to `connector.py` unpackaged.
`Addons.xcu` scopes the "Claude" menu entry + toolbar button to Calc and Writer only.

### Configuration and key storage

[src/config.py](../src/config.py) persists non-secret settings (model, temperature,
max_tokens, timeout, base_url, anthropic_version, ca_file) as JSON in a per-user
directory (`%APPDATA%\LibreOffice-Claude-Connector\config.json` on Windows), merged
over `DEFAULTS`; unknown keys on disk are ignored and a missing/corrupt file silently
falls back to defaults. `client_kwargs(cfg)` maps that dict onto `ClaudeClient(...)`'s
constructor keywords.

[src/keystore.py](../src/keystore.py) stores the Anthropic API key **separately from
the JSON config, and never in plaintext in it**. `get_api_key()` resolves in order:

1. The `ANTHROPIC_API_KEY` environment variable (developer/CI override) — takes
   precedence even if a key is also stored on disk.
2. A stored key in the config directory:
   - **Windows** — encrypted at rest with **DPAPI**, called via `ctypes`
     (`crypt32.CryptProtectData`/`CryptUnprotectData`, `CRYPTPROTECT_UI_FORBIDDEN` so
     it never pops a UI), base64-encoded on disk as `apikey.dpapi`. Per-user: only the
     same Windows user account can decrypt it.
   - **Other OSes** — base64 only, in `apikey.plain` (`0600` where supported) — this
     is a documented, explicit limitation, **not** encryption; the env var is
     recommended there instead.

`set_api_key()` always clears the other format's file first, so switching platforms
(or re-saving) never leaves a stale key file behind in the wrong format.

## Cross-version Python target

Per the locked decision in [BUILD-PLAN.md](BUILD-PLAN.md) ("Target Python 3.8/3.9
stdlib only"), the client avoids syntax and stdlib features newer than Python 3.8, so
the same source runs unmodified across the Python versions bundled with LibreOffice
24.8 → 25.8 (3.9 → 3.11). This has been verified in Phase 1 against LibreOffice
25.2.3.2's bundled Python 3.10.17 — see [DEVELOPMENT.md](DEVELOPMENT.md) for how to
reproduce that verification.
