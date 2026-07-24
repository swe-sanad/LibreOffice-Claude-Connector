<p align="center">
  <img src="docs/logo.png" alt="LibreOffice ↔ Claude Connector" width="180">
</p>

# LibreOffice ↔ Claude Connector

Two complementary open-source integrations between **LibreOffice** and Anthropic's
**Claude**, both built on the UNO API and LibreOffice's own bundled Python interpreter —
no external runtime, no third-party Python packages:

1. **The `.oxt` extension** — embeds Claude *inside* LibreOffice: select a range in
   Calc or text in Writer, give Claude an instruction, get the transformed result
   written straight back into your document.
2. **The MCP server** (`mcp/libreoffice_mcp.py`) — the inverse: lets Claude Code /
   Claude Desktop (or any MCP client) reach **in** and drive LibreOffice as a tool,
   with **170 tools** covering documents, Calc data/formulas/formatting/charts, Writer
   text/tables/styles/structure, embedded Basic macros, drawing shapes, window
   screenshots, and a raw UNO escape hatch. See [docs/MCP-TOOLS.md](docs/MCP-TOOLS.md)
   for the full catalog.

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
- [x] Additional commands (summarize, translate, fix grammar, generate formula, explain range)
- [ ] Public release on extensions.libreoffice.org

## The MCP server

`mcp/libreoffice_mcp.py` implements MCP's JSON-RPC-over-stdio transport by
hand — standard library only, runs under LibreOffice's bundled Python so the `uno`
module just works. It survives office restarts (automatic bridge reconnect).

**Connecting to LibreOffice — pipe → socket → auto-launch** (as of v0.7.0): the
server tries, in order, (1) the **agent-acceptor extension's named pipe** — which
reaches a LibreOffice you simply opened, no flags at all; (2) the classic loopback
socket on `LO_UNO_PORT`; (3) failing both, it **launches LibreOffice itself** with
the socket accept argument (`LO_AUTOSTART=0` to disable, `LO_HEADLESS=1` for
headless, `LO_SOFFICE` to pin the executable). `lo_status` reports which transport
connected. Install the `.oxt` (below) to get the flag-free pipe path; the socket +
auto-launch path needs nothing installed. You only need to register the server:

- **Claude Code — one command, via the plugin marketplace:**

  ```
  /plugin marketplace add swe-sanad/LibreOffice-Claude-Connector
  /plugin install libreoffice-connector@libreoffice-connector-marketplace
  ```

- **Claude Desktop — the `.mcpb` bundle:** download
  `libreoffice-connector-<version>.mcpb` from the latest GitHub release (or build it:
  `python scripts/build_mcpb.py`), double-click / drag it into Claude Desktop's
  Settings ▸ Extensions, and point the prompt at your LibreOffice bundled Python.

- **Manual registration** (any MCP client). For Claude Code:

   ```powershell
   claude mcp add libreoffice -e LO_UNO_PORT=2002 -- "C:\Program Files\LibreOffice\program\python.exe" "<repo>\mcp\libreoffice_mcp.py"
   ```

   or in `.mcp.json` / Claude Desktop config:

   ```json
   {
     "mcpServers": {
       "libreoffice": {
         "type": "stdio",
         "command": "C:/Program Files/LibreOffice/program/python.exe",
         "args": ["<repo>/mcp/libreoffice_mcp.py"],
         "env": {"LO_UNO_PORT": "2002"}
       }
     }
   }
   ```

**Highlights** (beyond the usual read/write-range fare — the full list is in
[docs/MCP-TOOLS.md](docs/MCP-TOOLS.md)):

- `lo_screenshot` — PNG of the real LibreOffice **window** (PrintWindow), because PDF
  export can differ from what the GUI actually renders.
- `reload_document` — store → close → reload: the serialization ground-truth check.
- `run_macro`, `basic_module` — invoke and manage a document's embedded Basic.
- `calc_list_shapes` / `calc_delete_shape`, `calc_sheet_properties` (RTL, visibility,
  frozen panes), `calc_set_validation`, `inspect_ods` (regex over the saved file's
  XML), and `uno_exec` — a Python escape hatch with the live UNO bridge in scope.
- Sheet arguments accept indexes, exact names, or the English token of bilingual
  `english | عربي` tab names; errors name the exception type and list real sheets;
  stdio is UTF-8 (Arabic-safe) on Windows.

Battle-tested by building a complete bilingual RTL data-entry workbook
([docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md) documents the field reports that shaped v0.5.0).

### The agent-acceptor extension (flag-free connect)

The `.oxt` (build: `python scripts/build_oxt.py`; install via **Tools ▸ Extension
Manager**, restart) runs a Job at office startup that opens a per-user **named
pipe** from inside LibreOffice — so any office you open normally is reachable with
no `--accept` flag and no port. Local-only (named pipe, never TCP), does not keep
the office alive, and disables with `CLAUDE_AGENT_ACCEPTOR=0`. See
[docs/SECURITY.md](docs/SECURITY.md). [docs/UPSTREAMING.md](docs/UPSTREAMING.md)
maps the road from here to **native agent support in LibreOffice core**.

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
- **Quick commands** (the `Claude` menu, alongside *Transform*): **Summarize
  Selection**, **Translate Selection…** (asks for a target language), **Fix Grammar
  & Spelling** — in both Calc and Writer — plus **Generate Formula…** and **Explain
  Range** in Calc. Each reuses the same read → Claude → write-back plumbing with a
  canned instruction (no free-text prompt needed).
- A modal progress dialog appears while Claude is contacted; the document is only read
  and written on the main thread, so LibreOffice itself never blocks on the network.

### GitHub Copilot / VS Code

If you are using GitHub Copilot in VS Code, this repo now includes a workspace MCP
configuration at `.vscode/mcp.json`. Open the workspace and Copilot agents can discover
`libreoffice` directly through the repo-native launcher in `mcpb/index.js`.

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

These **83** tests (Claude client, Calc transform + translate/fix-grammar/formula/
explain logic, Writer transform/summarize/translate/fix-grammar/prompt logic, plus
config + DPAPI keystore round-trip tests) mock `urllib`/use fake clients
entirely, or use a temp directory for config/keystore — no API key, no network access,
and no running LibreOffice needed.

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

## Contributing

Contributions are welcome — especially new tools. **[CONTRIBUTING.md](CONTRIBUTING.md)**
has the add-a-tool recipe and how to test it, and **[docs/TOOLS-WANTED.md](docs/TOOLS-WANTED.md)**
is a ready pick-list of 85 proposed Calc/Writer tools (each mapped to the exact UNO API),
with 🥇 good-first-tool picks to start from.

## Project layout

```
LibreOffice-Claude-Connector/
├── mcp/             libreoffice_mcp.py (stdio MCP server, 161 tools — see docs/MCP-TOOLS.md)
├── docs/            RESEARCH.md, BUILD-PLAN.md, ARCHITECTURE.md, DEVELOPMENT.md, CHANGELOG.md,
│                    MCP-TOOLS.md (generated tool reference), TOOLS-WANTED.md (roadmap),
│                    KNOWN-GAPS.md, TEST-PLAN.md, SECURITY.md, UPSTREAMING.md
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

- [docs/MCP-TOOLS.md](docs/MCP-TOOLS.md) — the MCP server's full tool reference (generated)
- [docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md) — field reports and the wishlist that became v0.5.0
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — technical design and layering
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — how to develop and test
- [docs/RESEARCH.md](docs/RESEARCH.md) — fact-checked research (architecture, UNO API, prior art, risks)
- [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) — the 7-phase build plan and locked decisions
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — release history

## License

[MIT](LICENSE) — MIT License.
