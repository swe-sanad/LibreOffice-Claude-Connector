# CLAUDE.md — LibreOffice ↔ Claude Connector

Guidance for Claude Code working in this repo. Keep it lean; deep detail lives in
`docs/`.

## What this project is

Two complementary connectors between **Anthropic's Claude** and **LibreOffice**
(Calc + Writer), built for Windows, targeting a **public** release (MPL-2.0):

1. **The `.oxt` extension** (`src/` + `ext/`) — embeds Claude *inside* LibreOffice.
   The user selects cells/text and runs **Claude ▸ Transform Selection with Claude…**
   (also in Tools ▸ Add-Ons, a toolbar button, and a sidebar panel); Claude rewrites
   the selection in place. Standalone — no Claude Code needed.
2. **The MCP server** (`mcp/libreoffice_mcp.py`) — the *inverse*: lets Claude Code /
   Desktop / Cowork drive a running LibreOffice as a tool (like the Figma MCP).

## Status (as of the latest commit)

Phases 0–5 complete **and verified against real LibreOffice 25.2.3.2 / bundled
Python 3.10.17**, plus the MCP server and the sidebar panel:

- ✅ Core Claude client, Calc + Writer transform logic, UNO bridge.
- ✅ Packaged `.oxt` with menu + Tools-Add-Ons + toolbar + **sidebar deck/panel**,
  in-app settings, and **Windows-DPAPI** API-key storage.
- ✅ MCP server (7 tools) — protocol + live-tool tests pass; a demo drives a real
  sheet via `tools/call`.
- ✅ 65 offline unit tests pass; all UNO integration tests pass; the `.oxt` installs
  and both the ProtocolHandler and the sidebar factory register.
- ✅ An adversarial multi-agent review found 10 issues; **all fixed & re-verified**.
- **Not yet verified / done:** the sidebar panel's *visual* render (factory is
  confirmed registered; appearance needs a human — see `docs/TEST-PLAN.md`); a live
  Claude call from the GUI dialogs (needs an API key); Phase 6/7 (streaming, richer
  in-panel prompt UI, publish to extensions.libreoffice.org).

## Locked decisions (don't relitigate)

- **Calc-first**, v1 feature = **rewrite/transform the selection**.
- **Claude-native** Messages API (thin seam, no multi-provider abstraction yet).
- **Standard library only** — no `requests`, no SDK, no compiled wheels (must run in
  LibreOffice's bundled Python). Target **Python 3.8** syntax for cross-version
  support (LO 24.8=3.9 … 25.8=3.11); the manifest gates install to LO ≥ 7.2.
- **Trigger via menu/toolbar/sidebar macros, NOT a Calc `=CLAUDE()` Add-In** (AI
  calls are slow/paid/non-deterministic — wrong fit for the recalc engine).
- **API key via Windows DPAPI** (`ctypes`, zero-dep), env-var override, never in the
  JSON config, never hardcoded.

## Repository layout

```
src/                         # extension source (single source of truth)
  claude_client.py           # stdlib Anthropic Messages client (retries, errors, TLS)
  calc_actions.py            # PURE Calc grid transform (prompt + tolerant JSON parse)
  writer_actions.py          # PURE Writer rewrite/generate + output cleaning
  uno_bridge.py              # UNO glue: connect + Calc/Writer read/write helpers
  uno_ui.py                  # AWT dialogs + off-UI-thread run_with_progress
  config.py / keystore.py    # JSON settings / DPAPI-encrypted API key
  connector.py               # registered ProtocolHandler component (menu/toolbar dispatch)
  sidebar_panel.py           # registered XUIElementFactory component (sidebar deck/panel)
ext/                         # .oxt packaging: description.xml, META-INF/manifest.xml,
                             #   Addons.xcu, ProtocolHandler.xcu, registry/.../{Sidebar,Factories}.xcu, icons/
mcp/libreoffice_mcp.py       # stdlib MCP server (JSON-RPC/stdio), runs under LO python.exe
scripts/                     # build_oxt.py, install_and_verify.ps1, run_integration.ps1,
                             #   make_icons.py, start_office_socket.ps1, spike_http.py
tests/                       # 4 offline suites (65 tests) + tests/integration/ (real LO)
docs/                        # RESEARCH, BUILD-PLAN, ARCHITECTURE, DEVELOPMENT, CHANGELOG, TEST-PLAN
```

**Packaging note:** the registered components (`connector.py`, `sidebar_panel.py`)
live at the `.oxt` root; the helper modules are bundled as a **`claudeconn` package
under `pythonpath/`** to avoid top-level name collisions. `connector.py` imports
package-or-flat (`try: from claudeconn import … except ImportError: import …`), which
is also why the offline tests can import the flat `src/` modules.

## Common commands (PowerShell, from repo root)

LibreOffice: `C:\Program Files\LibreOffice\program\` (python.exe, soffice.exe, unopkg.com).

```powershell
# Offline unit tests (65, no key/network/office)
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v

# Real-LibreOffice integration test (isolated headless profile; one at a time)
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_calc_uno.py
#   others: test_writer_uno.py, test_mcp_tools.py, demo_mcp_client.py, test_calc_end_to_end.py (needs key)

# Build + install into an isolated profile + verify dispatch/sidebar-factory register
powershell -ExecutionPolicy Bypass -File scripts\install_and_verify.ps1

# Build the .oxt, then install into the REAL profile (restart LO to activate)
& "C:\Program Files\LibreOffice\program\python.exe" scripts\build_oxt.py
& "C:\Program Files\LibreOffice\program\unopkg.com" add --suppress-license -f dist\claude-connector-0.1.0.oxt

# MCP server: start LO with a socket, then register with Claude Code (see mcp/README.md)
powershell -ExecutionPolicy Bypass -File scripts\start_office_socket.ps1
```

`docs/TEST-PLAN.md` is the full QA checklist (automated + manual GUI).

## Gotchas (hard-won — see docs/DEVELOPMENT.md + project memory)

- **Test UNO edits against a REAL headless LibreOffice** — logic bugs (e.g. the
  cursor-collapse reversal) are invisible to mocked tests. `run_integration.ps1`
  spins up an isolated instance for this.
- **Cursor:** call `cursor.collapseToEnd()` after each `insertString`/
  `insertControlCharacter`, or multi-line inserts reverse.
- **`setDataArray`** needs the 2-D array to match the range exactly and rejects
  `None` — coerce `None`/JSON-null → `""`.
- **Extensions activate on the NEXT LibreOffice start**, not the one they're
  installed during (the isolated harness does a warm-up boot). XCU changes may need
  a clean `unopkg remove` + `add` + restart.
- **Kill leftover HEADLESS test instances** with `Where MainWindowHandle -eq 0`;
  **never force-kill a GUI office** (`-ne 0`, unsaved work). Zombies cause flaky
  "port never opened" boots.
- **Network calls run off the UI thread** (`uno_ui.run_with_progress`); document
  reads/writes stay on the main thread. Don't touch UNO from the worker thread.

## Working style here

- The user (Sanad) drives; confirm scope on forks. Commit at phase/feature
  boundaries with Conventional Commit messages (local git; no remote configured —
  don't push unless asked). Keep `docs/CHANGELOG.md` current.
- After a real bug/lesson, capture it in project memory and `docs/`.
- Don't re-add the extension to the user's real profile or restart their LibreOffice
  without saying so — they may have unsaved work.
