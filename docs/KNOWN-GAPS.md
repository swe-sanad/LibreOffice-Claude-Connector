# Known gaps & bugs — field report (2026-07-19, Kahatayn session)

Observed while driving `main.ods` of the Kahatayn project (33 sheets, bilingual
`english | عربي` tab names, embedded Basic app) through the MCP server
(`mcp/libreoffice_mcp.py`). Each item is reproducible; fix in a future session.

## Bugs

### 1. `sheet` param rejects non-ASCII (Arabic) sheet names
- `calc_read_range {sheet: "view_dashboards | لوحة المؤشرات"}` → `Error:` (empty).
- Same call with an ASCII name (`_form_meta`) works.
- Arabic **cell content coming OUT of** the server round-trips fine, so the
  break is host→server: most likely stdin of the stdio transport decoded with
  the Windows locale codepage (cp1252) instead of UTF-8 inside LibreOffice's
  bundled Python — the mangled string then misses `sheets.getByName()`.
- Fix direction: force UTF-8 on the stdio pipes
  (`sys.stdin.reconfigure(encoding="utf-8")` / `io.TextIOWrapper(..., "utf-8")`)
  at server start; add a round-trip test with an Arabic sheet name.

### 2. `sheet` param rejects integer indices
- `calc_read_range {sheet: 0}` → `Error:` (empty), despite `_resolve_sheet()`
  having an `isinstance(sheet, int)` branch. Probable cause: the number arrives
  as a float (or string) after JSON/schema handling, falls through to
  `getByName("0.0")`. Fix: coerce numeric-looking values
  (`int(float(sheet))`) before the name lookup, and cover both in tests.

### 3. Empty error messages
- All the failures above surface as a bare `Error:` — UNO exceptions
  (`NoSuchElementException`, `IndexOutOfBoundsException`, disposed-bridge) often
  have an empty `Message`, and the handler forwards `str(e)` verbatim.
- Fix: report `type(e).__name__` + repr, and on sheet-resolution failure append
  the list of available sheet names (that alone would have made bugs 1–2
  self-explanatory from the client side).

### 4. Stale UNO bridge after a LibreOffice crash/restart
- After soffice died and was relaunched, every call kept failing with
  `Binary URP bridge already disposed` until the server itself was restarted.
- Commit `94cc49b` ("auto-reconnect the UNO bridge") may already address this —
  verify it catches `DisposedException` from the *cached* bridge on each call
  path, drops the cache, and re-resolves (the running instance predated it).

### 5. Server died opening a macro-containing document
- `open_document` on the macro-embedded `main.ods` → MCP error
  `Connection closed` (server process gone). Suspects: macro-security
  interaction blocking the bridge, or an unhandled exception in the load path.
- Fix: pass `MacroExecutionMode` (honor trusted locations / never prompt) and a
  non-interactive `InteractionHandler` in the `loadComponentFromURL` args, and
  make the dispatch loop survive a failed tool call.

## Not-dynamic-enough tools

- `insert_form_control` inserts only on the **active** sheet — no `sheet`
  param — and can only wire a `url`; no way to attach a script event
  (`vnd.sun.star.script:` via `XEventAttacherManager`) or set arbitrary control
  properties (colors, multiline, anchor cell).

## Missing tools (each one forced a side-script through LO's python.exe this session)

| Wanted tool | Why |
|---|---|
| `calc_set_active_sheet` | prerequisite for every active-sheet-only tool |
| `calc_list_controls` / `calc_delete_control` | inspect & clean form controls (couldn't tell what buttons existed) |
| `run_macro` (Basic name or script-URI dispatch) | e.g. fire the embedded `KahataynForms.Engine.RefreshView` |
| `calc_sheet_visibility` | show/hide `_form_meta`-style hidden sheets |
| `calc_get_formatting` | read back styles/number formats to verify formatting work |
| `uno_exec` (escape hatch: run a short UNO Python snippet) | covers everything above until dedicated tools exist |

## Repro environment
Windows 11, LibreOffice 25.x bundled Python, server launched via
`C:/Program Files/LibreOffice/program/python.exe`, `LO_UNO_PORT=2002`,
workbook `E:\Volunteer\Kahatayn\...\main.ods` (bilingual tabs, embedded Basic).
