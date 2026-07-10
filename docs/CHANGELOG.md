# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
