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


## Session 2 additions (2026-07-19, Kahatayn RTL/dashboards work)

New bugs learned (LibreOffice-level, documented in the Kahatayn project memory,
not connector fixes): form-control shapes are silently dropped by the ODS
writer on RTL sheets (use draw shapes + OnClick scripts instead); RTL sheets
use a negative-x mirrored shape coordinate space.

`lo_screenshot` shipped (commit b0ea964) — remove it from the wishlist.

### Tools that would have made this session dramatically faster

**UPDATE: all shipped in v0.5.0** (`reload_document`, `run_macro`,
`calc_list_shapes`/`calc_delete_shape`, `calc_set_active_sheet`,
`calc_sheet_properties`, `calc_set_validation`, `basic_module`, `inspect_ods`,
`uno_exec`), together with the three session-1 P1 bug fixes (bilingual/int
sheet resolution, blank error messages, UTF-8 stdio). Table kept for history:

| Wanted tool | Pain it removes |
|---|---|
| `run_macro(name, args?)` | invoke embedded Basic (RefreshView, Save_person, Ping compile-probe) — today needs a side-script through LO python |
| `reload_document` (store -> close -> load) | THE missing verification: in-memory state lies; only a reload reveals what actually serialized (lost buttons bug) |
| `list_shapes(sheet)` / `delete_shape` | instantly shows what's really on a DrawPage (found the dropped-controls bug by hand-scripting exactly this) |
| `set_active_sheet` + `scroll_to(cell)` | every GUI verification needed controller scripting; select() alone doesn't scroll |
| `sheet_properties(get/set)` | TableLayout (RTL), IsVisible, freeze rows/cols — all hand-scripted this session |
| `calc_set_validation(range, list/hint)` | cell validity dropdowns + input help — built via side-script |
| `basic_modules(list/get/set)` + compile check | manage embedded Basic libraries; a syntax error silently kills every macro |
| `inspect_ods(path, xpath/grep)` | grep content.xml inside the saved zip — how the root cause was actually found |
| `uno_exec(snippet)` | escape hatch that subsumes all of the above until dedicated tools exist |


## Session 3 additions (2026-07-23, Nasaq Arabic Writer proposal)

First heavy **Writer** session (prior reports were Calc). Built a multi-section
Arabic (RTL) technical+financial proposal — ~45 `writer_*` calls, logo image,
15+ tables. Bugs/gaps below are reproducible; TODO for a future session.

### Bugs / reliability

1. **Focus-stealing breaks every write — no way to target a document.**
   All `writer_*`/`calc_*` tools act on the *implicit* active document
   (`desktop.getCurrentComponent()`). Mid-build the source doc closed and an
   already-open Calc file grabbed focus; the very next `writer_append_text`
   died with `The active document is not a Writer document.` Had to re-activate
   the Writer doc by hand via `uno_exec`
   (`...getCurrentController().getFrame().activate()`). This is the single
   biggest Writer-session hazard: any user click or background doc event
   silently redirects writes.
   - Fix (either/both):
     - `set_active_document(title | url)` tool — the Writer analogue of the
       shipped `calc_set_active_sheet`.
     - Optional `document` (title/url/id) param on every read/write tool that
       resolves against `desktop.getComponents()` instead of trusting focus.

2. **Transient failure indistinguishable from validation error.**
   A valid `writer_insert_table` (7×3, 21 cells) failed once with
   `data is larger than the table (7x3)` and succeeded on identical retry — the
   real cause was the safety classifier being unavailable
   (`claude-sonnet-5 ... temporarily unavailable`), surfaced as a domain error.
   Related to session-1 bug #3 (blank errors): callers can't tell "retry me"
   from "your input is wrong." Fix: tag transient/infra failures distinctly from
   argument-validation failures in the message.

### Missing / not-dynamic-enough (Writer)

| Wanted | Pain it removes |
|---|---|
| `set_active_document(title\|url)` | see bug 1 — prerequisite for reliable multi-doc Writer sessions |
| RTL / writing-direction control | **big one for Arabic.** No way to set paragraph `WritingMode = RL_TB` or `ParaAdjust` per-direction. `writer_format_paragraph` exposes `align` but not direction; tables + numbers render LTR. Add `direction: rtl\|ltr` to `writer_format_paragraph`, and a page/table-level RTL toggle (`TableColumnRelativeSum` mirrors, `TextTable` RTL). |
| batch op (`writer_batch` / render-outline) | a 3-section doc = ~45 sequential round-trips (one per heading/paragraph/table). Accept an array of ops, or a structured outline → paragraphs+tables in one call. |
| `caption` param on `writer_insert_table` | every table needs a following "جدول N — …" caption as a separate append; couple it to the insert (real Writer caption via `SetReferenceMark`/sequence field). |
| echo active-doc title in every tool response | drift (bug 1) only surfaces as a *later* failed write; returning the active title each call makes it visible immediately. |
| cursor positioning / edit-in-place | everything appends to end — can't insert mid-document or edit an existing table cell after the fact. |

### The rebuild tax (structural edits are impossible → discard & rebuild)

Confirmed live: when the proposal's whole theme changed, there was **no way to
edit structure** — only append. `writer_find_replace` (+ search-based
`writer_format_text`/`writer_format_paragraph`) can swap a word/phrase or
restyle a match, but there is **no** way to:

- delete a paragraph or a **range** of paragraphs,
- delete a table, or reshape one (add/remove rows/columns, edit a cell after insert).

So any change beyond word-level — reordering sections, dropping a subsection,
turning a 6×2 table into a 7×3 — means **discarding the entire document and
rebuilding it from scratch** (~45 calls again). That "rebuild tax" is the single
biggest productivity sink of a long Writer session.

Note: the enabling tools are **already** on the wishlist in
[TOOLS-WANTED.md](TOOLS-WANTED.md) — `writer_edit_table` (P1),
`writer_delete_object` (P1), `writer_set_paragraph_text` (P2),
`writer_get_paragraphs` (P1). What this session adds is (a) the **priority
signal** — ship these before more feature tools; the append-only model makes
iterative work punishing — and (b) one genuinely missing primitive:
`writer_delete_paragraphs(from, to)` / a range delete, which no wishlist item
currently covers.

---

# Session 5 field report (2026-07-23, Arabic proposal `عرض-فني-ومالي-نسق`)

Driving a 17-page bilingual RTL Writer proposal. Two of the top pains recorded
above are now **CLOSED**, plus a paper-cut and a test-harness gotcha.

## Shipped this session (137 → 144 tools)

### Paragraph structure + RTL

- **`writer_set_text_direction`** — sets `rtl`/`ltr`. Default flips the WHOLE
  document in one call: every body paragraph + every table-cell paragraph + the
  page style (`WritingMode` RL_TB/LR_TB + matching `ParaAdjust`). This retires
  the manual `uno_exec` RTL flip that every Arabic session re-implemented by
  hand. `start`/`count` restrict it to a paragraph range; `align=false` keeps
  alignment (e.g. a centered title); `tables`/`page=false` narrow the scope.
- **`writer_delete_paragraphs(start, count)`** — the "genuinely missing
  primitive" called out under *the rebuild tax*. Deletes a paragraph range
  including its breaks; handles the mid-document, through-the-last, and
  delete-everything (leaves one empty paragraph) cases. Structural reordering /
  dropping a subsection no longer means discarding and rebuilding the doc.
- **`writer_format_paragraph` now targets by index** — new `start`/`count`
  (0-based, the `writer_get_paragraphs` index space), taking precedence over
  `search`. Restyling one heading by index (e.g. fixing a stray empty
  `Heading 1`) was previously only doable via `uno_exec`.

These three are covered by `check_writer_paragraph_ops` in
`tests/integration/test_mcp_tools_extended.py` — verified end-to-end against a
real headless office (paragraph/cell/page `WritingMode`, delete-range sequences,
index restyle).

### Menu coverage — one tool per remaining menu (Table / Format / Style / Form / Tools)

- **`writer_sort_table`** (Table) — sort a table's data rows by a key column,
  numeric-aware, header pinned. Reads the grid, sorts in Python, writes back.
- **`writer_edit_table` now sets cell text** (Table) — `cell` + `text` edits a
  cell after insert, closing the "can't edit an existing table cell" gap.
- **`writer_change_case`** (Format) — upper/lower/title/sentence over a `search`
  match or a paragraph range.
- **`writer_apply_style`** (Style) — apply a named paragraph style (search or
  index range) OR a named **character** style (search) — the char-style-apply
  gap `writer_format_text` never covered. Pairs with `set_style` (create).
- **`form_control`** (Form) — `list` all controls, or `set` an existing one's
  label/value/state/enabled/read_only/items. Works on Writer + Calc.
- **`writer_set_chapter_numbering`** (Tools) — bind the first N outline levels to
  a numbering scheme so Heading 1/2/3 auto-number 1 / 1.1 / 1.1.1. (Impl note:
  `ChapterNumberingRules.replaceByIndex` must be called via `uno.invoke` with an
  explicit `[]com.sun.star.beans.PropertyValue` Any, mutating the level's
  existing structs in place — a plain tuple or `_pv`-rebuilt structs both throw
  `IllegalArgumentException`.)

All six covered by `check_menu_coverage_tools` in the same test file.

## Still open (Writer)

- Convert text ↔ table; in-cell table formula.
- Figure/table caption insert.
- `writer_move_paragraphs` (reorder) — delete now exists; move does not.

## Test-harness gotcha (not a server bug, but bit this session)

`scripts/run_integration.ps1` launches an isolated office on port 2002, but the
server's **pipe-first** `_connect()` ladder will hijack onto a *live*
agent-acceptor office if one is running (its pipe wins over the harness socket).
Symptom: the test printed `connected over pipe 'lo-claude-sanad'` and a
pre-existing `check_writer` assertion failed on contaminated live-office state.
Fix when running the harness alongside a live session: set **`LO_UNO_PIPE=0`**
to force the socket rung. Worth having `run_integration.ps1` export that itself
so the harness is always self-isolating.
