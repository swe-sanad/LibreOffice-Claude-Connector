# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **v0.5.0 — the Kahatayn-session wishlist, implemented (10 new tools + 3 bug fixes).**
  New tools: `reload_document` (store→close→reload — the serialization ground-truth
  check), `run_macro` (invoke document Basic by name/URI), `calc_list_shapes` /
  `calc_delete_shape` (DrawPage inspection incl. OnClick scripts), `calc_set_active_sheet`
  (activate + select + scroll), `calc_sheet_properties` (RTL/visible/freeze),
  `calc_set_validation` (dropdown lists + hints), `basic_module` (list/get/set embedded
  Basic), `inspect_ods` (regex over the saved zip's XML), `uno_exec` (Python escape
  hatch with the live bridge in scope). Fixes: `_resolve_sheet` now accepts int/float/
  numeric-string indexes AND matches bilingual `english | عربي` tab names by English
  token, and raises listing the actual sheets; tool errors always name the exception
  type (UNO exceptions often have an empty message); stdio forced to UTF-8 on Windows
  (Arabic arguments were mangled by the cp1252 default). All tools exercised live
  against the Kahatayn workbook, including a full reload round-trip.

- **`lo_screenshot` MCP tool** — saves a PNG of the LibreOffice *window* itself
  via Win32 `PrintWindow` (auto-restores a minimized window, DPI-aware physical
  pixels, works while the window is behind others; pure ctypes + zlib PNG, no
  Pillow). This is the only reliable way to see what the GUI **actually
  renders**: PDF export can differ from the screen — discovered when form
  controls on RTL sheets rendered in PDF but were silently dropped from the
  screen/file. Params: `path` (default temp dir), `window_title` (substring,
  default "LibreOffice"). Windows-only.

### Fixed

- **Hardening pass following an adversarial multi-agent code review (10 confirmed findings, all fixed and re-verified).**
  - [src/calc_actions.py](../src/calc_actions.py) `_parse_json_lenient` now parses the
    whole (fence-stripped) JSON reply first, falling back to bracket-span extraction only
    if that fails — a valid grid with stray `{}`/`[]` in a cell value or surrounding
    prose no longer fails to parse.
  - [src/calc_actions.py](../src/calc_actions.py) `transform_range` caps the selection at
    `MAX_CELLS` (5000 cells), raising `TransformError` before sending a whole-column
    selection that would freeze the UI or blow the token budget; also raises a clear
    `TransformError` when Claude's reply was truncated (`result.truncated`) instead of
    parsing a partial grid.
  - [src/uno_ui.py](../src/uno_ui.py) `run_with_progress` returns a `CANCELLED` sentinel
    when the user dismisses the progress dialog mid-call; [src/connector.py](../src/connector.py)
    checks for it on both the Calc-transform and Writer-rewrite paths instead of falling
    through to a misleading "Unexpected error" message box.
  - [src/uno_bridge.py](../src/uno_bridge.py) `get_calc_selection_range` now raises
    `SelectionError` on a multi-range (Ctrl-selected) Calc selection instead of silently
    operating on only the first contained range; added `range_cell_count` so
    `connector.py` can enforce the selection-size cap before calling Claude.
  - [src/writer_actions.py](../src/writer_actions.py) now appends a visible note to the
    inserted text when Claude's reply was truncated (previously inserted silently with no
    indication it was cut off).
  - [src/connector.py](../src/connector.py) now passes the user's configured `max_tokens`
    through to the Writer generate-at-caret path (previously hard-capped at 1024
    regardless of settings).

### Security

- [src/keystore.py](../src/keystore.py) `_write_private` creates the API-key file with
  mode `0o600` via `os.open` at creation time (instead of `chmod` after the fact), closing
  the brief window on POSIX where the key file was world-readable between creation and
  permission-tightening.
- [src/config.py](../src/config.py) `load_config` now type-coerces and validates every
  value read from disk, so a hand-edited config (e.g. `"timeout": "120"` as a string)
  produces a graceful fallback to the default instead of a raw `TypeError` surfacing later.
- [src/claude_client.py](../src/claude_client.py) rejects a non-HTTPS `base_url` (except
  `localhost`, for local dev/testing) at construction time, so the API key can never be
  sent in cleartext over the network; also honors a server `retry-after` header up to a
  120s cap on 429 responses.
- [ext/description.xml](../ext/description.xml) declares
  `LibreOffice-minimal-version` 7.2 — the first LibreOffice release bundling Python 3.8 —
  so the extension cannot install onto an older LibreOffice and fail at import time.
- Found via an adversarial multi-agent code review; all 10 confirmed findings above are
  fixed and re-verified: the full offline suite is **65 tests, all passing**, and the
  Calc + Writer UNO integration tests and the installed-extension dispatch test all pass
  against real LibreOffice 25.2.3.2.

### Added

- **Phase 4-5: packaged, installable `.oxt` extension with in-app settings and secure
  key storage.**
  [src/connector.py](../src/connector.py) — the registered UNO component: a
  `com.sun.star.frame.ProtocolHandler` implementing `XDispatchProvider`/`XDispatch`/
  `XInitialization`/`XServiceInfo`, exposing command URLs
  `com.swepioneers.claudeconnector:Transform` and `:Settings`. It reads the selection
  on the main thread, runs the Claude call on a worker thread via
  `uno_ui.run_with_progress` (a modal progress dialog whose completion is marshalled
  back to the main thread with `com.sun.star.awt.AsyncCallback`), then performs the
  document write back on the main thread; any error becomes an AWT message box.
- [src/uno_ui.py](../src/uno_ui.py) — AWT message boxes, a modal instruction-prompt
  dialog, a settings dialog (model dropdown + masked API-key field), and
  `run_with_progress`, all built from `UnoControlDialogModel` controls.
- [src/config.py](../src/config.py) — JSON user settings (model, temperature,
  max_tokens, timeout, base_url, anthropic_version, ca_file) persisted per-user (e.g.
  `%APPDATA%\LibreOffice-Claude-Connector\config.json`), merged over defaults;
  `client_kwargs()` maps the config onto `ClaudeClient(...)`.
- [src/keystore.py](../src/keystore.py) — API key storage, deliberately separate from
  the JSON config: `ANTHROPIC_API_KEY` env var takes precedence; otherwise the key is
  stored **encrypted with Windows DPAPI** (via `ctypes`, zero third-party
  dependencies) on Windows, or a documented-as-NOT-encrypted base64 file on other
  platforms. The key is never written to the JSON config.
- `ext/` extension scaffold: `description.xml`, `META-INF/manifest.xml`, `Addons.xcu`
  (a top-level "Claude" menu + a toolbar button, scoped to Calc + Writer),
  `ProtocolHandler.xcu`, `description/desc_en.txt`, and generated `icons/*.png`.
- Helper modules (`claude_client`, `calc_actions`, `writer_actions`, `uno_bridge`,
  `config`, `keystore`, `uno_ui`) are bundled as the `claudeconn` package under
  `pythonpath/` inside the `.oxt` to avoid top-level module-name collisions with other
  extensions; `connector.py` tries the packaged import first, falling back to a flat
  import for local/dev runs.
- [scripts/build_oxt.py](../scripts/build_oxt.py) — assembles the installable `.oxt`
  from `ext/` + `src/`. [scripts/make_icons.py](../scripts/make_icons.py) generates the
  extension's icons (stdlib only). [scripts/install_and_verify.ps1](../scripts/install_and_verify.ps1)
  builds the `.oxt`, installs it into an isolated profile, does a warm-up boot (an
  installed extension only activates on the *next* boot), then a second boot that
  verifies both dispatch commands resolve.
- [tests/test_config_keystore.py](../tests/test_config_keystore.py) — offline tests
  for `config` (defaults-merging, save/load) and `keystore` (including a real DPAPI
  encrypt/decrypt round-trip on Windows asserting the key is never stored in
  plaintext). [tests/integration/test_extension_dispatch.py](../tests/integration/test_extension_dispatch.py)
  — a LIVE integration test confirming the installed extension's ProtocolHandler
  resolves both command URLs.
- Verified: the full offline suite is now **65 tests, all passing** on LibreOffice's
  bundled Python 3.10.17. `install_and_verify.ps1`'s full build → install → warm-up
  boot → test boot flow **passed against real LibreOffice 25.2.3.2**, with both
  dispatch commands confirmed to resolve — the full packaging + import chain works
  end to end.

- **Phase 3: Writer rewrite-selection + generate-at-caret.**
  [src/writer_actions.py](../src/writer_actions.py) — pure, UNO-free, network-free
  text logic mirroring `calc_actions`: `build_rewrite_system_prompt`/
  `build_rewrite_user_prompt`, `build_generate_system_prompt`, `clean_output` (unwraps
  a whole-output markdown fence but deliberately preserves surrounding quotes/inline
  backticks — a legitimately quoted rewrite is not damaged), `default_max_tokens(text)`
  (scales the output budget to input length, bounded to `[512, 8192]`), `rewrite_text(
  client, selected_text, instruction, ...)`, and `generate_text(client, instruction, ...)`.
- [src/uno_bridge.py](../src/uno_bridge.py) — Writer section added: `is_writer(doc)`,
  `get_writer_selection(doc)` (reads the view cursor; `isCollapsed()` is the reliable
  no-selection signal), `replace_writer_selection`/`insert_writer_at_caret`, a
  multi-paragraph-aware `_insert_multiline` helper (splits on `\n` into real
  `PARAGRAPH_BREAK` control characters), `_with_undo` (groups mutations into one named
  undo step via `getUndoManager()`), and a synchronous `rewrite_writer_selection(doc,
  client, instruction)` that rewrites the selection or generates at the caret when
  nothing is selected.
- [tests/test_writer_actions.py](../tests/test_writer_actions.py) — offline unit tests
  for `writer_actions` (no UNO, no network, no key required).
- [tests/integration/test_writer_uno.py](../tests/integration/test_writer_uno.py) — a
  LIVE integration test driving a real headless LibreOffice Writer: reads the
  selection, replaces it, verifies a `\n` in the replacement becomes a real paragraph
  break, and exercises caret-detection + insert-at-caret when nothing is selected.
- Verified: the full offline suite is now **42 tests, all passing** on LibreOffice's
  bundled Python 3.10.17. The Writer UNO integration test **passed against real
  LibreOffice 25.2.3.2** (selection read/replace, multi-paragraph insert, and
  caret-insert all confirmed end to end); the Calc integration test was re-run
  alongside it and still passes (no regression).
- [scripts/run_integration.ps1](../scripts/run_integration.ps1) hardened: it now
  pre-kills any stale test instance (matched by a unique profile marker, so it never
  touches a normal LibreOffice window), uses a 150s cold-start budget, and tears down
  reliably.

### Fixed

- **UNO text-cursor collapse bug in multi-paragraph inserts.** After
  `XText.insertString`/`insertControlCharacter`, the text cursor still *spans* the
  just-inserted text rather than collapsing to its end. Left unhandled, a multi-line
  insert came out reversed/garbled (observed: inserting `"Line one\nLine two"`
  produced paragraphs `['', 'Line twoLine one']`). Fixed in `uno_bridge._insert_multiline`
  by calling `cursor.collapseToEnd()` after every insert. This was only caught by the
  real-LibreOffice integration test — see the "Gotchas" section in
  [DEVELOPMENT.md](DEVELOPMENT.md).

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
