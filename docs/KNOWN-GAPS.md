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

## Shipped this session (137 → 151 tools)

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

### Structural editing — finishing the set

- **`writer_move_paragraphs`** (start, count, to) — reorder a paragraph block via
  `.uno:MoveUp`/`.uno:MoveDown` (preserves content + formatting; works headless).
- **`writer_convert_table`** (`to_text`/`to_table`) — table → rows-of-paragraphs
  (cells joined by a separator), or paragraphs → table (split on a separator).
  Both done Python-side (read/parse → insert → delete) rather than via a dialog
  dispatch, so they're deterministic.
- **`writer_insert_caption`** — auto-numbering caption ("Figure 1 — …") backed by
  a per-category `SetExpression` SEQUENCE field, so numbers increment across
  captions of the same category (verified 1, 2).
- **`set_style` gained `follow_style`** — set a paragraph style's next-paragraph
  style (e.g. a heading followed by body), so style chains can be built.

Covered by `check_structural_tools` in the test file.

### Niche tools (Table / Format / Tools)

- **`writer_table_formula`** — set a formula in a table cell (`=<A1>+<A2>`,
  `sum <A1:A5>`) and return the computed value (XCell.setFormula).
- **`writer_split_cells`** — split a cell (or `A1:B1` range) into N cells along
  columns or rows (table cursor `splitRange`).
- **`writer_clear_formatting`** — reset direct char/para formatting to the
  underlying style (`setAllPropertiesToDefault`) over a match or paragraph range.
- **`writer_set_line_numbering`** — turn document line numbering on/off with
  interval/options (`getLineNumberingProperties`).

Covered by `check_niche_tools` in the test file.

## Still open (Writer)

- Deeper table ops: repeat-heading-rows across page breaks.
- Format: autoformat/autocorrect-apply. Tools: autotext, bibliography,
  hyphenation/thesaurus config, digital-signature *creation*.
- Form authoring: data binding, design-mode toggle, form navigator.
- These are the genuinely low-value / high-complexity long tail — build on demand.

## Still open (Calc / cross-cutting)

- The `sheet` param rejects Arabic/non-ASCII names + integer indices, and errors
  come back empty (Session-1 bugs 1–3) — still unfixed; bites bilingual work.
- Version-sensitive tools implemented best-effort, never verified live:
  `calc_create_pivot`, `calc_add_scale_format`, `calc_add_sparkline`,
  `calc_multiple_operations`, `writer_mail_merge` — need a live-office pass.

## Test-harness gotcha (not a server bug, but bit this session)

`scripts/run_integration.ps1` launches an isolated office on port 2002, but the
server's **pipe-first** `_connect()` ladder will hijack onto a *live*
agent-acceptor office if one is running (its pipe wins over the harness socket).
Symptom: the test printed `connected over pipe 'lo-claude-sanad'` and a
pre-existing `check_writer` assertion failed on contaminated live-office state.
Fix when running the harness alongside a live session: set **`LO_UNO_PIPE=0`**
to force the socket rung. Worth having `run_integration.ps1` export that itself
so the harness is always self-isolating.

---

# Session 6 field report (2026-07-23) — reliability keystone + power tools (151 → 154)

- **`set_active_document`** (title | url | index) — **closes Session-3 bug #1**
  (focus-stealing). Activates a chosen open document so subsequent reads/writes
  target it, via `component.getCurrentController().getFrame().activate()`; verified
  by switching focus between a live Writer and Calc doc and confirming each tool
  lands on the activated one. This is the reliability keystone for multi-document
  sessions — no more "The active document is not a Writer document".
- **`writer_replace_image`** — swap an image's graphic (new file path via
  GraphicProvider) and/or resize it in place, by name (e.g. update a logo).
- **`writer_repeat_heading_rows`** — set a table's first N rows to repeat as a
  header on every page it spans (`RepeatHeadline` + `HeaderRowCount`).

Covered by `check_doc_activation_tools`. Deliberately NOT built (genuinely
low-value / external-resource-bound / dialog-only — build on demand): bibliography
DB, thesaurus, autotext, digital-signature creation, form data-binding.

---

# Session 7 field report (2026-07-23) — full-tool field test: 12 fixes + 2 expansions

Drove **every** Writer, Calc and shared tool by building a real Writer status
report + a 3-sheet Calc dashboard (RTL Arabic profile), then diffing observed vs
expected. Surfaced 10 bugs (several of the dangerous "silent-success" / wrong-
target class), a few rough edges, and two discovery gaps. All fixed and covered by
`check_fieldtest_fixes` in `tests/integration/test_mcp_tools_extended.py`.

## Root-cause themes (each fixed once, in a shared place)
1. **Plain tuple vs typed UNO sequence** — a bare Python tuple handed to a UNO API
   that wants `[]com.sun.star...` is silently marshalled as the wrong type: the
   call no-ops or throws. New helper `_any_seq(type_name, items)` →
   `uno.Any("[]"+type, tuple)`; applied to `calc_sort_range` (SortFields) and
   `bind_document_event` (Events). (`writer_set_chapter_numbering` already did it.)
2. **Silent success** — tools reported OK while doing nothing or leaving error
   cells. `calc_set_formulas` now scans for `Err:`/`#NAME?` and returns them;
   `writer_apply_list` errors when its range matches no paragraph; `calc_sort_range`
   really sorts now.
3. **`doc.createInstance` returns `None`** where the office **service manager** is
   required — `ShapeCollection` in `calc_group_shapes`.
4. **Locale sensitivity** — the function-argument separator (`;` not `,`) and the
   absence of English list-STYLE names. `calc_set_formulas` detects the separator
   at runtime; `writer_apply_list` drives bullets via `NumberingRules`.
5. **Focus-based active-doc resolution** — `getCurrentComponent()` lags MCP-driven
   creation, so `close_document` once closed the **wrong** document.

## Bugs fixed
1. **`close_document` closed the WRONG doc** (data-loss risk). Repro: open proposal
   → `create_document(calc)` → `close_document` closed the proposal, not the calc.
   Fix: optional `index`/`title`/`url` target (shared `_select_doc`); `create_document`
   /`open_document` now `activate()` the new doc.
2. **`calc_set_formulas` — comma formulas silently `#NAME?`/`Err:508`** on ';'
   locales. Fix: runtime separator detection (`_arg_separator`, probed on a
   throwaway temp sheet, cached) + quote-aware `_normalize_formula` + error scan.
3. **`calc_sort_range` — silent no-op.** Fix: typed-`uno.Any` SortFields.
4. **`bind_document_event` — `IllegalArgumentException`.** Fix: typed-`uno.Any`
   via `uno.invoke`.
5. **`writer_apply_list` — silent no-op on localized builds** (styles `List 1`/
   `Numbering 1`, not `List Bullet`). Fix: apply `NumberingRules` directly.
6. **`calc_group_shapes` — `AttributeError: 'NoneType'...add`.** Fix: create the
   `ShapeCollection` from `smgr.createInstanceWithContext(...)`.
7. **`set_view_zoom` — `AttributeError: ZoomValue`.** Fix: zoom target is the
   controller for Calc, `ctrl.ViewSettings` for Writer (`_zoom_target`).
8. **`writer_clear_formatting` (search) — uncaught UNO crash** when a match is in
   the header/footer. Fix: use each match's own `getText()`.
9. **`writer_add_conditional_section visible=false` didn't hide.** Fix: set
   `Condition`/`IsVisible` after insertion, on the section fetched by name.
10. **`calc_multiple_operations` — `Err:522`** when the layout puts the formula
    inside the filled range. Fix: reject an overlapping `formula_range` with a
    clear message + error scan.

## Rough edges fixed
- **Enum reprs leaked** (`<Enum instance …('CENTER')>`) from `_jsonable` →
  now `.value` (`"CENTER"`/`"LIST"`); fixes `calc_get_cell_format`,
  `calc_get_validation`, `calc_get_conditional_formats`.
- **`calc_get_conditional_formats`** omitted the range — the entry exposes it via
  the `Range` property (no `getRange()` on this build).
- **`get_signatures`** masked a `CannotConvertException` (URL string ≠ `XStorage`)
  — now opens the doc storage first.

## Expansions (closed gaps)
- **`writer_list_objects`** now lists draw shapes (rectangle/ellipse/line/text) —
  previously creatable + deletable-by-name but invisible to discovery.
- **`writer_insert_table`** gained a `search`/`after_index` anchor for positional
  insert (was append-at-end only; `convert_table` was the only workaround).

## Verified already-closed
- **`_resolve_sheet`** already handles integer indices, exact names, and bilingual
  `english | عربي` tabs (Session-1 bug). Added an Arabic-sheet assertion to lock it.

All of the above are asserted against a real LibreOffice in `check_fieldtest_fixes`
(run with `LO_UNO_PIPE=0` alongside a live agent-acceptor office). `set_view_zoom`
is verified where a view exists (skipped only if the harness office is viewless).
