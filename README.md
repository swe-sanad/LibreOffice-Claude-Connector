# LibreOffice ↔ Claude Connector

An open-source LibreOffice extension that embeds Anthropic's **Claude** directly into
**Calc** (first) and **Writer**, via the UNO API and LibreOffice's own bundled Python
interpreter — no external runtime, no third-party Python packages. Select a range or
some text, give Claude an instruction, and get the transformed result written straight
back into your document.

## Status

**Phase 4-5 complete: packaged, installable `.oxt` extension with in-app settings and
secure key storage — verified end to end against real LibreOffice.** The connector now
ships as a real extension: a `Claude` menu and toolbar button in Calc and Writer, wired
through a registered UNO `ProtocolHandler`, with a `Settings…` dialog for picking the
model and pasting an Anthropic API key (stored encrypted at rest, never in plaintext
config). The network call runs on a worker thread behind a modal progress dialog so the
UI never freezes. See [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) for the full 7-phase plan
and [docs/RESEARCH.md](docs/RESEARCH.md) for the underlying research (UNO API,
LibreOffice's bundled Python, the Claude Messages API, and prior-art review).

## Features

- [x] Zero-dependency Claude Messages API client (stdlib `urllib` + `json` + `ssl` only)
- [x] Typed error handling, retries with backoff, `retry-after` support
- [x] Calc: select a range, transform it with Claude, write results back
- [x] Writer: select text and rewrite it with Claude in place, or generate new text at
      the caret when nothing is selected
- [x] Packaged `.oxt` extension with a `Claude` menu/toolbar button (Calc + Writer)
- [x] In-app settings (model picker, API key stored encrypted via Windows DPAPI)
- [x] Network call runs off the UI thread, behind a modal progress dialog
- [ ] Additional commands (summarize, translate, fix grammar, generate formula, explain range)
- [ ] Public release on extensions.libreoffice.org

## Requirements

- LibreOffice (24.8 or later) — this project targets LibreOffice's **bundled Python**
  interpreter, not a system Python. No `pip install` is required or supported inside
  that interpreter.
- Code targets **Python 3.8+ standard library only**, so it runs unmodified across the
  bundled interpreters shipped with LibreOffice 24.8 → 25.8 (Python 3.9 → 3.11).

## Installation

1. Build the `.oxt` from source (needs the bundled Python only — no `pip`):

   ```powershell
   & "C:\Program Files\LibreOffice\program\python.exe" scripts\build_oxt.py
   ```

   This writes `dist/claude-connector-<version>.oxt`. (Or download a pre-built `.oxt`
   from a release, once one is published.)

2. In LibreOffice: **Tools ▸ Extension Manager ▸ Add…**, pick the `.oxt`, accept the
   license. Alternatively, from a shell: `unopkg add path\to\claude-connector-*.oxt`.

3. **Restart LibreOffice.** Extensions register their menu/toolbar/dispatch config on
   the *next* start after install — the menu will not appear until you restart.

## Usage

- **Calc**: select one or more cells, then **Claude ▸ Transform Selection with
  Claude…** (also on the toolbar). Type an instruction (e.g. "uppercase these" or "add
  10% to each number"); the selection is replaced with Claude's reply, same shape.
- **Writer**: select some text and use the same command to rewrite it in place, or
  place the cursor with nothing selected to generate new text at the caret.
- A modal progress dialog appears while Claude is contacted; the document is only read
  and written on the main thread, so LibreOffice itself never blocks on the network.

## Configuration & API key

**Claude ▸ Settings…** opens a dialog to:

- Pick the model (a curated dropdown — `claude-haiku-4-5`, `claude-sonnet-5`,
  `claude-opus-4-8` — the field also accepts a free-text model id).
- Paste your Anthropic API key. The key field is masked, and leaving it blank keeps
  whatever key is already stored.

The key is **never** written to the JSON settings file. It is resolved in this order:

1. `ANTHROPIC_API_KEY` environment variable, if set (developer/CI override).
2. Otherwise, a key saved via the Settings dialog: on Windows this is encrypted at
   rest with **DPAPI** (per-user, via `ctypes` — no third-party dependency); on other
   platforms it currently falls back to a base64-encoded file, which is **not**
   encryption (documented limitation — prefer the env var there for now).

Other settings (model, timeout, base URL, API version) live in
`%APPDATA%\LibreOffice-Claude-Connector\config.json`.

## Development

Run the offline unit tests with LibreOffice's bundled Python (adjust the path for your
LibreOffice install):

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

These **62** tests (14 for the Claude client, 17 for the Calc transform logic, the
Writer transform/prompt-building logic, plus config + DPAPI keystore round-trip tests)
mock `urllib`/use fake clients entirely, or use a temp directory for config/keystore —
no API key, no network access, and no running LibreOffice needed.

To exercise the Calc UNO read/write path against a real, isolated headless LibreOffice
(no API key needed — the Claude call is a deterministic stub):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_calc_uno.py
```

To exercise the same for Writer (selection read/replace, multi-paragraph inserts,
caret-insert):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_writer_uno.py
```

To prove live connectivity (TLS, headers, error parsing) from the bundled interpreter,
set `ANTHROPIC_API_KEY` and run the smoke-test spike:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."
& "C:\Program Files\LibreOffice\program\python.exe" scripts\spike_http.py
```

To build the `.oxt` and verify it installs and its dispatch commands resolve against a
real, isolated LibreOffice instance:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_and_verify.ps1
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full development workflow,
including the "LibreOffice caches Python modules" gotcha and the extension-testing
gotchas (activation-on-next-boot, warm-up boot).

## Project layout

```
LibreOffice-Claude-Connector/
├── docs/            RESEARCH.md, BUILD-PLAN.md, ARCHITECTURE.md, DEVELOPMENT.md, CHANGELOG.md
├── src/             claude_client.py (Claude Messages API client)
│                    calc_actions.py (pure Calc transform/prompt/parse logic)
│                    writer_actions.py (pure Writer rewrite/generate/prompt logic)
│                    uno_bridge.py (UNO glue: selection read/write for Calc and Writer)
│                    connector.py (registered UNO ProtocolHandler / dispatch component)
│                    uno_ui.py (message boxes, prompt + settings dialogs, worker-thread progress runner)
│                    config.py (JSON user settings in %APPDATA%)
│                    keystore.py (API key storage: env var override, else DPAPI-encrypted on Windows)
├── ext/             extension scaffold: description.xml, META-INF/manifest.xml, Addons.xcu
│                    (Claude menu + toolbar), ProtocolHandler.xcu, description/, icons/,
│                    pythonpath/claudeconn/ (bundled copies of the helper modules above)
├── scripts/         spike_http.py (live Claude smoke test)
│                    run_integration.ps1 (launches isolated headless LO, runs a UNO integration test)
│                    build_oxt.py (assembles dist/claude-connector-<version>.oxt)
│                    make_icons.py (generates ext/icons/*.png, stdlib only)
│                    install_and_verify.ps1 (build → install → warm-up boot → verify dispatch resolves)
└── tests/           test_claude_client.py, test_calc_actions.py, test_writer_actions.py,
                     test_config_keystore.py (offline unit tests)
                     integration/test_calc_uno.py, integration/test_writer_uno.py,
                     integration/test_extension_dispatch.py (live UNO integration tests)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — technical design and layering
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — how to develop and test
- [docs/RESEARCH.md](docs/RESEARCH.md) — fact-checked research (architecture, UNO API, prior art, risks)
- [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) — the 7-phase build plan and locked decisions
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — release history

## License

[MPL-2.0](LICENSE) — Mozilla Public License, Version 2.0.
