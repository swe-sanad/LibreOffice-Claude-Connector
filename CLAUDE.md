# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Keep this lean; deep detail lives in `docs/`.

## What this project is

Two complementary connectors between **Anthropic's Claude** and **LibreOffice**
(Calc + Writer), built for Windows, MIT, targeting a public release:

1. **The `.oxt` extension** (`src/` + `ext/`) — embeds Claude *inside* LibreOffice.
   The user selects cells/text and runs **Claude ▸ Transform Selection with Claude…**
   (also in Tools ▸ Add-Ons, a toolbar button, and a sidebar panel); Claude rewrites
   the selection in place. Standalone — no Claude Code needed.
2. **The MCP server** (`mcp/libreoffice_mcp.py`) — the *inverse*: lets Claude Code /
   Desktop / Cowork drive a running LibreOffice as a tool (like the Figma MCP).
   **137 tools**; registered with Claude Code at user scope as `libreoffice`.

## Status

Verified against real **LibreOffice 25.2.3.2 / bundled Python 3.10.17**:

- ✅ `.oxt`: menu + Tools-Add-Ons + toolbar + **sidebar deck/panel (render confirmed
  in Calc & Writer)**, in-app settings, Windows-DPAPI API-key storage.
- ✅ MCP server: **137 tools**, all exercised against a real office by
  `tests/integration/test_mcp_tools_extended.py`; protocol + core tool tests pass.
- ✅ 65 offline unit tests; all UNO integration tests; `.oxt` installs and both the
  ProtocolHandler and sidebar factory register.
- **Needs an API key (not done here):** a live Claude Transform from the GUI dialogs.
- **Not done:** Phase 6/7 (streaming, richer in-panel prompt UI, publish to
  extensions.libreoffice.org).

## Locked decisions (don't relitigate)

- **Standard library only** — no `requests`, no SDK, no compiled wheels (must run in
  LibreOffice's bundled Python). Target **Python 3.8** syntax (LO 24.8=3.9 … 25.8=3.11);
  the manifest gates install to LO ≥ 7.2.
- **Claude-native** Messages API (thin seam, no multi-provider abstraction).
- **Trigger via menu/toolbar/sidebar macros, NOT a Calc `=CLAUDE()` Add-In** (AI calls
  are slow/paid/non-deterministic — wrong fit for the recalc engine).
- **API key via Windows DPAPI** (`ctypes`, zero-dep), env-var override, never in the
  JSON config, never hardcoded.
- **`src/` is the single source of truth.** The build copies it into the `.oxt`; do not
  hand-edit anything under `dist/`.

## Repository layout

```
src/                    # extension source (single source of truth)
  claude_client.py      # stdlib Anthropic Messages client (retries, errors, TLS)
  calc_actions.py       # PURE Calc grid transform (prompt + tolerant JSON parse)
  writer_actions.py     # PURE Writer rewrite/generate + output cleaning
  uno_bridge.py         # UNO glue: connect + Calc/Writer read/write helpers (shared with MCP)
  uno_ui.py             # AWT dialogs + off-UI-thread run_with_progress
  config.py/keystore.py # JSON settings / DPAPI-encrypted API key
  connector.py          # registered ProtocolHandler component (menu/toolbar dispatch)
  sidebar_panel.py      # registered XUIElementFactory component (sidebar deck/panel)
ext/                    # .oxt packaging: description.xml, META-INF/manifest.xml, Addons.xcu,
                        #   ProtocolHandler.xcu, registry/.../{Sidebar,Factories}.xcu, icons/
mcp/libreoffice_mcp.py  # stdlib MCP server (JSON-RPC/stdio, 137 tools), runs under LO python.exe
scripts/                # build_oxt.py, install_and_verify.ps1, run_integration.ps1,
                        #   start_office_socket.ps1, make_icons.py, spike_http.py
tests/ tests/integration/# offline suites (65) + real-LO integration tests
docs/                   # RESEARCH, BUILD-PLAN, ARCHITECTURE, DEVELOPMENT, CHANGELOG, TEST-PLAN
```

**Two entry points, one bridge.** Both the extension and the MCP server drive UNO
through `src/uno_bridge.py`. The extension calls it in-process from a document it
already has; the MCP server calls it over a socket against whatever document is open.

**Packaging.** The registered components (`connector.py`, `sidebar_panel.py`) live at
the `.oxt` root; helper modules are bundled as a **`claudeconn` package under
`pythonpath/`** to avoid top-level name collisions. `connector.py` imports
package-or-flat (`try: from claudeconn import … except ImportError: import …`), which
is also why the offline tests import the flat `src/` modules directly.

**Sidebar wiring is fragile — four files must agree** or the deck silently never
appears: `sidebar_panel.py` (`IMPL_NAME`), `Sidebar.xcu` (deck/panel + the
`private:resource/toolpanel/ClaudeSidebar/ClaudePanel` URL), `Factories.xcu`
(`ClaudeSidebar` → impl name), and `manifest.xml` (registers all three).

## Common commands (PowerShell, from repo root)

LibreOffice lives at `C:\Program Files\LibreOffice\program\` (python.exe, soffice.exe,
unopkg.com). All Python must run under **that** python.exe (it has `uno`).

```powershell
# Offline unit tests (65; no key/network/office). Single test:
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest tests.test_writer_actions.TestRewrite -v

# MCP protocol smoke test (no office) — should report "137 tools"
& "C:\Program Files\LibreOffice\program\python.exe" mcp\test_mcp_protocol.py

# Real-LibreOffice integration test (isolated headless profile; run ONE at a time)
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_calc_uno.py
#   others: test_writer_uno.py, test_mcp_tools.py, test_mcp_tools_extended.py, demo_mcp_client.py
#           test_calc_end_to_end.py (needs ANTHROPIC_API_KEY)

# Build + install into an isolated profile + verify dispatch & sidebar-factory register
powershell -ExecutionPolicy Bypass -File scripts\install_and_verify.ps1

# Build the .oxt, then install into the REAL profile (fully restart LO to activate)
& "C:\Program Files\LibreOffice\program\python.exe" scripts\build_oxt.py
& "C:\Program Files\LibreOffice\program\unopkg.com" add --suppress-license -f dist\claude-connector-0.1.0.oxt

# MCP server: start LO with a UNO socket (fully close LO first), then it's usable
powershell -ExecutionPolicy Bypass -File scripts\start_office_socket.ps1   # → localhost:2002
```

`docs/TEST-PLAN.md` is the full QA checklist (automated + the manual GUI steps).

## Gotchas (hard-won — see docs/DEVELOPMENT.md + project memory)

- **The integration harness isolates via the `UserInstallation` ENV VAR, not the
  `-env:UserInstallation=` switch** — that switch is silently dropped on this Windows
  build and the office boots the user's REAL profile (so tests, and the teardown
  `terminate()`, hit the user's session). The harness sets the env var, then verifies
  over UNO that the office is really the isolated profile before touching it.
- **`getCurrentComponent()` returns None in headless/unfocused sessions** even with
  docs open — fall back to enumerating `desktop.getComponents()` (see
  `libreoffice_mcp._current_doc`).
- **Kill leftover HEADLESS test instances** with `Where MainWindowHandle -eq 0`;
  **never force-kill a GUI office** (`-ne 0`, unsaved work). Headless zombies cause
  flaky "port never opened" boots; a fresh-profile FIRST boot can take minutes.
- **Extensions activate on the NEXT LibreOffice start**, not the install-time one (the
  harness does a warm-up boot). Stale sidebar/menus: `unopkg remove … && add … && restart`.
- **Sidebar panels need `XSidebarPanel.getHeightForWidth` + a resize-listener relayout**
  — the parent window is 0×0 at creation, so a one-shot layout yields an invisible panel.
- **Cursor:** `cursor.collapseToEnd()` after each `insertString`/`insertControlCharacter`,
  or multi-line inserts reverse. **`setDataArray`** needs an exact-shape 2-D array and
  rejects `None` — coerce `None`/JSON-null → `""`.
- **UNO layout props are 1/100 mm**; LibreOffice round-trips through twips, so values
  come back ±1–2 (15mm → 1499). Assert with tolerance.
- **Network calls run OFF the UI thread** (`uno_ui.run_with_progress`); document
  reads/writes stay on the main thread. Don't touch UNO from the worker thread.

## Working style here

- Local git, **no remote** — don't push. Commit at feature boundaries with Conventional
  Commit messages; keep `docs/CHANGELOG.md` current. Work lands on `master`.
- Don't re-add the extension to the user's real profile or restart their LibreOffice
  without saying so — they may have unsaved work.
- After a real bug/lesson, capture it in project memory and `docs/`.
