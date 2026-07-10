# Test Plan / QA Handoff — LibreOffice ↔ Claude Connector

A self-contained checklist for an agent (or human) to verify the whole project on
a Windows machine with LibreOffice installed. Two deliverables are covered:

1. **The `.oxt` extension** — embeds Claude into Calc & Writer (menu / toolbar /
   Tools-Add-Ons / sidebar).
2. **The MCP server** — lets Claude Code / Cowork drive LibreOffice as a tool.

> Nothing here is destructive to the user's documents, **except** that some steps
> ask you to restart LibreOffice — make sure the user has saved their work first,
> and **never force-kill a GUI LibreOffice** (see Cleanup).

---

## 0. Environment & prerequisites

| Thing | Value on the reference machine |
|---|---|
| Repo root | `E:\SWE-Pioneers\LibreOffice-Claude-Connector` |
| LibreOffice | 25.2.3.2, `C:\Program Files\LibreOffice\program\` |
| Bundled Python | 3.10.17 → `C:\Program Files\LibreOffice\program\python.exe` |
| `unopkg` | `C:\Program Files\LibreOffice\program\unopkg.com` |
| API key (for LIVE tests only) | env var `ANTHROPIC_API_KEY` (not required for most tests) |

Run all commands from the **repo root** in **PowerShell**. Adjust paths if
LibreOffice is installed elsewhere (`(Get-Command soffice).Source` won't help —
find `soffice.exe` under `Program Files`).

Which tests need an API key?
- **No key needed:** offline unit tests, all UNO integration tests, MCP protocol +
  tool tests, install-and-verify. (These use mocks / stub transforms / a fake key.)
- **Key needed:** `scripts/spike_http.py`, `tests/integration/test_calc_end_to_end.py`,
  and the manual live "Transform" in the GUI.

---

## 1. Automated tests (no API key, no GUI)

### 1a. Offline unit suite — expect **65 passing**
```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```
PASS = `Ran 65 tests ... OK`. Covers the Claude client, Calc/Writer transform logic,
config, and the DPAPI keystore (real encrypt/decrypt round-trip on Windows).

### 1b. MCP protocol smoke test (no office)
```powershell
& "C:\Program Files\LibreOffice\program\python.exe" mcp\test_mcp_protocol.py
```
PASS = `MCP handshake ok; tools/list has 50 tools (...); ping ok.`

### 1c. Real-LibreOffice integration tests (headless, isolated profile)
Each spins up its own throwaway headless LibreOffice, runs, and tears down. Run
them **one at a time** (each takes ~30–120 s; first boot of a fresh profile can be
slow). Expect `EXIT: 0` and `... CHECKS PASSED` for each.
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_calc_uno.py
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_writer_uno.py
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_mcp_tools.py
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_mcp_tools_extended.py
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\demo_mcp_client.py
```
- `test_calc_uno` — read/select/write a range; None→"" coercion.
- `test_writer_uno` — read selection, replace, multi-paragraph, insert-at-caret.
- `test_mcp_tools` — the core MCP tool functions drive Calc (read/write/status).
- `test_mcp_tools_extended` — the full 50-tool set: document lifecycle
  (create/open/save-as xlsx+docx/PDF export/close), Calc formulas, structure
  (insert/delete rows+columns, copy, clear, find&replace, used range), sheets,
  formatting, merge, charts, selection, conditional formatting, cell comments,
  **range borders**; Writer headings/outline, append, find&replace,
  format-matches, tables, images, page breaks, comments, conditional sections,
  **paragraph styling, page styling (size/orientation/margins/columns),
  header/footer, table formatting**; and **form controls** (button/checkbox/…)
  in both Calc and Writer.
- `demo_mcp_client` — acts as an MCP client and drives LibreOffice via `tools/call`
  (reads A1:A3, writes B1:B3). Prints the JSON-RPC round-trip.

### 1d. Extension build + install + registration (isolated profile)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_and_verify.ps1
```
This builds the `.oxt`, installs into an isolated profile, does a **warm-up boot**
(extensions activate on the *next* start), then a test boot. PASS output includes:
```
dispatch Transform  -> RESOLVED
dispatch Settings   -> RESOLVED
sidebar factory   -> INSTANTIATED
```
Meaning: the ProtocolHandler + the sidebar panel factory both load and register.
(It does **not** prove the sidebar *renders* — that's the manual check in §2.)

---

## 2. Extension — manual GUI checks (needs a human looking at the screen)

The extension is already installed in the user's **real** profile. To make changes
visible you must fully restart LibreOffice.

**Restart:** close all LibreOffice windows **and** exit the tray Quickstarter
(right-click the tray icon → Exit). Then reopen LibreOffice.

Checklist (open a Calc sheet, then a Writer doc):

- [ ] **Menu bar** shows a top-level **Claude** menu with *Transform Selection with
      Claude…* and *Settings…*.
- [ ] **Tools ▸ Add-Ons** shows the same two entries.
- [ ] A **toolbar button** is present.
- [ ] **Sidebar**: the right-edge vertical icon strip (Properties/Styles/Gallery/…)
      has a **Claude** icon. Click it → a **Claude** deck opens with a panel
      containing **Transform Selection with Claude** and **Settings…** buttons.
      *(If the icon/panel is missing or blank, that's the known unverified bit —
      report it; a remove+re-add of the extension + restart clears stale config.)*
- [ ] **Claude ▸ Settings…** opens a dialog with a model dropdown + a masked
      API-key field. Enter a key + pick a model → Save → "Settings saved.".
- [ ] **Calc transform:** type text in A1:A3, select it, Claude ▸ Transform, enter
      an instruction (e.g. `uppercase every cell`) → a "Contacting Claude…" progress
      box → the cells are transformed. *(needs a valid API key)*
- [ ] **Writer rewrite:** type a sentence, select it, Transform, instruction
      `make it more formal` → selection is replaced. *(needs key)*
- [ ] **Writer generate:** put the caret (no selection), Transform, instruction
      `write one sentence about cats` → text inserted at the caret. *(needs key)*
- [ ] **Sidebar buttons** do the same as the menu.

Error-handling checks (no key needed except where noted):
- [ ] No API key set → Transform shows a clear "No Anthropic API key…" message.
- [ ] Select an entire column (click the `A` header) → Transform → clear
      "Selection is too large (…)" message (no freeze).
- [ ] Ctrl-select two separate ranges → Transform → "Please select a single
      contiguous range" message.
- [ ] During a live call, close the "Contacting Claude…" box → no crash / no
      "Unexpected error" (it just cancels).

---

## 3. Live end-to-end Claude call (needs an API key)

```powershell
# 1. HTTPS reachability from the bundled Python (prints the model's reply):
$env:ANTHROPIC_API_KEY = "sk-ant-..."
& "C:\Program Files\LibreOffice\program\python.exe" scripts\spike_http.py

# 2. Full loop through Calc UNO + a real Claude call:
powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_calc_end_to_end.py
```
Without a key both **SKIP** (exit 2) with a clear message. With a key, #2 seeds
A1:A3 = apples/bananas/cherries, asks Claude to uppercase, and asserts
`[APPLES, BANANAS, CHERRIES]` → `PASS`.

---

## 4. MCP server — drive LibreOffice from Claude Code

The MCP server lets an external Claude (Claude Code / Desktop / Cowork) control
LibreOffice. §1b–1c already proved the protocol + tools work headlessly. To try it
against a **live** session:

```powershell
# 1. Start LibreOffice (GUI, real profile) with a UNO socket.
#    Fully close LibreOffice first — it's single-instance; a 2nd launch won't
#    open the socket.
powershell -ExecutionPolicy Bypass -File scripts\start_office_socket.ps1
#    → LibreOffice opens listening on localhost:2002

# 2. Register the server with Claude Code (then open a NEW Claude Code session):
claude mcp add libreoffice --env LO_UNO_PORT=2002 -- `
  "C:/Program Files/LibreOffice/program/python.exe" `
  "E:/SWE-Pioneers/LibreOffice-Claude-Connector/mcp/libreoffice_mcp.py"
```
Then, in that new session, ask Claude Code things like *"use lo_status"*, *"read
A1:C10 of the open sheet"*, *"write a summary into E1"*. Tools available:
`lo_status`, `list_documents`, `get_current_selection`, `calc_read_range`,
`calc_write_range`, `writer_get_text`, `writer_replace_selection`. See `mcp/README.md`.

---

## 5. Cleanup & gotchas (read before running headless tests)

- **Kill leftover HEADLESS test instances** (safe — a real GUI office has a
  non-zero window handle and is never touched):
  ```powershell
  Get-Process -Name soffice, soffice.bin -EA SilentlyContinue |
    Where-Object { $_.MainWindowHandle -eq 0 } |
    ForEach-Object { Stop-Process -Id $_.Id -Force }
  ```
  Do this between integration runs if one hangs; accumulated headless zombies cause
  flaky "port never opened" first-boots.
- **Never force-kill a GUI LibreOffice** (`MainWindowHandle -ne 0`) — unsaved work.
- **Don't run the harness while a GUI office holds its port.** If LibreOffice was
  started with `start_office_socket.ps1` (port 2002), the integration harness's
  port is taken; both harness scripts now FAIL fast in that case ("port … already
  in use"). Close the GUI office or pass `-Port <free port>` to the harness.
- **Extensions activate on the NEXT boot**, not the one during which they were
  installed. The isolated harness does a warm-up boot for this reason.
- **First boot of a fresh profile can take tens of seconds to a couple minutes.**
- **XCU changes need a clean re-register:** if the sidebar/menus look stale,
  `unopkg remove com.swepioneers.libreoffice-claude-connector` then
  `unopkg add --suppress-license <dist\*.oxt>`, and restart.
- The **MCP server must run under LibreOffice's `python.exe`** (it needs `uno`),
  not system Python.

---

## 6. Status: verified vs. needs-human

**Verified automatically (all green on LibreOffice 25.2.3.2 / Python 3.10.17):**
- 65 offline unit tests; MCP protocol + tools; Calc/Writer UNO edits; the `.oxt`
  installs and both the ProtocolHandler and the sidebar factory register; the MCP
  round-trip edits a real sheet.

**Needs a human (can't be verified headlessly):**
- The menu/toolbar/Tools/sidebar actually *appearing* and looking right.
- The sidebar panel *rendering* (factory is confirmed registered; visuals are not).
- The interactive dialogs (instruction prompt, settings, progress) behaving well.
- A live Claude Transform end-to-end in the GUI (needs the user's API key).

**Not covered / known limits:**
- Non-Windows: DPAPI key encryption is Windows-only (other OSes fall back to a
  base64 file, documented as not-encrypted).
- Only tested on LibreOffice 25.2.3.2; the manifest gates install to LO ≥ 7.2.

---

## 7. What to report back
For each §1 command: the final line (`OK` / `EXIT: 0` / `PASS`) or the failure.
For §2/§3: which checkboxes passed, and a screenshot + the exact error text for any
that failed (especially the **sidebar** — whether the Claude deck icon appears and
the panel shows its two buttons). Note the LibreOffice version if not 25.2.3.2.
