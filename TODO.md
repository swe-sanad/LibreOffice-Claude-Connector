# TODO

## Upstream borrow backlog — triaged against our current surface

GitHub Copilot enumerated the tool surfaces of three sibling LibreOffice-MCP
projects (sandraschi, patrup, waterpistolai). Triaged below against what this
repo already ships — **161 tools** (Writer 62 / Calc 67 / cross-app 32). Most of
what was enumerated we already have; the genuine gaps are called out separately.

### Already covered — no action

- **patrup/mcp-libre** (whole set): `create_document_live`, `insert_text_live`,
  `format_text_live`, `save_document_live`, `export_document_live`,
  `get_document_info_live`, `get_text_content_live`, `list_open_documents` →
  already ours as `create_document`, `writer_append_text`, `writer_format_text`,
  `save_document`, `export_document`, `get_document_properties`,
  `writer_get_text`, `list_documents`. Nothing to borrow.
- **waterpistolai Calc/Writer core**: `open/new/save/close_document`,
  `get_sheet_names`, `get/set_cell_value`, `create_new_sheet`,
  `create_pivot_table`, `sort_range`, `create_chart`, `conditional_format`,
  `format_cell_range`, `apply_style`, `insert_text`, `insert_form_control`,
  `run_macro` → all shipped (`calc_list_sheets`, `calc_read/write_range`,
  `calc_add_sheet`, `calc_create_pivot`, `calc_sort_range`, `calc_create_chart`,
  `calc_add_conditional_format`, `calc_format_range`, `writer_apply_style`, …).
- **sandraschi**: `document_info` → `get_document_properties`, `status` →
  `lo_status`, `help` → MCP `tools/list`, `run_macro` → `run_macro` /
  `basic_module`. Covered.
- "Richer Calc helpers (formatting / conditional formatting / charts / pivots)" —
  already shipped in full.

### Pruned in — shipped (v0.9.2) → 170 tools

Full mapping in `docs/UPSTREAM-PARITY.md`.

- [x] **Headless conversion** — `convert` (single + batch).
- [x] **Merge** — `merge` (Writer/text docs; `pdf_merge` deferred — see below).
- [x] **Dispatcher facade** — `dispatch` (tool-count relief; fans out to all 170,
  does not replace the discrete tools).
- [x] **Templates** — `list_templates` + `create_from_template`.
- [x] **Python macros** — `run_python_macro` + `list_macros`.
- [x] **Calc convenience** — `calc_statistics`, `read_spreadsheet`.

### Deferred — new apps / out-of-scope (docs for a later or someone else's session)

> See `docs/UPSTREAM-PARITY.md` for the rationale on each.

- [ ] **LibreOffice Base (database)** — `run_query`, `list_tables`, `create_table`,
  `insert_data`, `create_report`, `create_form` (waterpistolai). A whole new app;
  the highest-value next frontier. Scope a `base_*` family + DB connection model.
- [ ] **Impress / Draw** — the other unsupported apps; no upstream to borrow from.
- [ ] `pdf_merge` — needs a PDF library (breaks stdlib-only).
- [ ] `batch_pack` (archive packaging), `watch_*` (doc/file watching),
  `live_type` (simulated typing) — niche/novelty.
- [ ] `bridge_discover/bridge_call` — mostly moot (our pipe-first `_connect`
  already reaches an extension-hosted office).

### From Nelson MCP (quazardous/nelson-mcp) — the ambitious target

> HTTP-based, 100+ tools, Writer/Calc/Draw/Impress. Full triage in
> `docs/UPSTREAM-PARITY.md`. Highest-leverage: **HTTP transport**.

- [ ] **HTTP (Streamable-HTTP/SSE) transport** — parity + remote clients (also in
  `docs/CROSS-AGENT.md`). The single most impactful item.
- [ ] **Persistent document IDs + per-call `_document` targeting** (id/path/title).
- [ ] **Structured errors** (`code/message/hint/retryable`) + enum "did-you-mean".
- [ ] **Tool presets / custom endpoints** (minimal/writer-edit/calc/…) — pairs
  with `dispatch` to tame the 170-tool count on smaller LLMs.
- [ ] Undo-wrapped mutations; `_resolved`/`_session` in responses + `/health`;
  batch variable-chaining; one-click client launchers; Calc `=PROMPT()` (`.oxt`).
- [ ] Out of core scope: tunnels/SSL (deployment), AI image generation (AI-content).

### From writeragent (KeithCu/writeragent) — the feature-dense target

> HTTP extension, Writer/Calc/Draw/Impress, deep data-science/quant layer. Full
> triage in `docs/UPSTREAM-PARITY.md`.

- [ ] Fits us: **HTTP transport** (shared with Nelson), **per-request `_document`
  targeting**, **MathML/LaTeX → Math object** (UNO, no lib), **format-preserving
  replace**, undo-wrapped rewrites.
- [ ] **Out of scope under stdlib-only** (needs NumPy/pandas/SciPy/SymPy/DuckDB/
  embeddings): the Calc DS/quant suite (`=PY()`, regression, monte-carlo,
  clustering, portfolio/LP, DuckDB SQL, plots), SymPy symbolic math, semantic
  search, OCR, web search, audio, image gen, grammar backends. `calc_statistics`
  covers the *basic* stats dependency-free. **Strategic fork:** matching this
  layer requires deciding to bundle third-party deps — see `docs/UPSTREAM-PARITY.md`.

### Already tracked elsewhere (dedupe)

- **HTTP/SSE transport**, **per-client config recipes**, and a
  **layout-independent launcher** → see `docs/CROSS-AGENT.md` "Opportunities".
- **Packaging / distribution** (MCPB build, `.vscode/mcp.json` for Copilot) —
  largely done; remainder is the per-client recipe matrix above.

### Not planned

- **OooDev-style abstraction layer** — we already have targeted helpers
  (`_pv`, `_any_seq`, `_resolve_sheet`, `_uno_enum/_uno_struct`, …). A heavy
  abstraction layer is boilerplate we don't need; revisit only if UNO repetition
  becomes a real maintenance cost.
- **Replacing discrete tools with a single dispatcher** — discrete tools give
  clearer per-tool schemas; keep them. (See the optional facade under P2 for the
  tool-count concern.)

### Validation (applies to every item above)

- [ ] Add regression tests (extend `tests/integration/test_mcp_tools_extended.py`)
  for each new capability before merging.
- [ ] Re-run the live Calc/Writer MCP verification flow after implementation.

## Found in real use — 2026-09-18, building an RTL proposal template (contracts repo)

Session: Claude Code drove the connector from `E:\Sanad-SWE\contracts` to build
`_Templates/مقترح خدمة برمجية - قالب.ott` and a filled ODT + PDF. Four bugs, one gap,
one design note. Reproduced against v0.9.8 (`lo_status` reported 52 of 191 advertised).

### Bugs

- [x] **`dispatch` with tool='list' returns only 15 tools (its own module).**
  Fixed in v0.10.0 / v0.10.1: iterates `_ALL_TOOL_DEFS` from `registry`. Tested.
- [x] **`list_documents` raises `NameError: tool_lo_status`.**
  Fixed in v0.10.1: correctly imported from `shared_recovery`.
- [x] **Duplicate definitions in `writer_text.py`:**
  Removed duplicates in v0.10.0.
- [x] **Probing a tool with `{}` mutates the document.**
  Fixed in v0.10.1: `dispatch` provides side-effect-free `tool='describe'` (`args={'tool': '...'}`) returning `inputSchema` and description.

### Gap

- [x] **`save_document` cannot write templates.**
  Fixed in v0.10.1: added `("writer", "ott") -> "writer8_template"` and `("calc", "ots") -> "calc8_template"`.

### Design note (not a bug)

- Building a 60-block, 7-table RTL document through discrete tools is ~80 round trips
  (`batch` helps). `uno_exec` + a builder script did it in one call; keep both paths, and
  document that a fresh `CharacterStyle` created over UNO renders Latin in caps until
  `CharCaseMap = 0` is set (LibreOffice default, worth a line in `set_style`'s docs).
- What was NOT a gap, after checking the source: `create_from_template`,
  `writer_set_text_direction` (whole doc incl. tables + page), `writer_set_page_style`
  (a4, margins), `writer_set_header_footer`, `writer_insert_field`, `writer_insert_toc`,
  `set_style` (all families), `writer_set_document_defaults` (CTL fonts),
  `writer_format_table`, `writer_repeat_heading_rows`, `writer_apply_list`,
  `writer_set_chapter_numbering`, `set_active_document`. They are simply not in the basic
  tier, and the truncated catalog hid them.
- [ ] **Lists: `writer_apply_list` attaches `NumberingRules` directly to paragraphs.** In practice
  the next paragraph typed or inserted after such an item inherits the list, and resetting it with
  `setPropertyToDefault("NumberingRules")` was unreliable (a heading picked up a "1.", another a
  bullet). The robust pattern is a named list style (`NumberingStyles`) bound to a paragraph style
  via `NumberingStyleName`. Consider: `set_style` accepting `list_style` for paragraph styles, and a
  `writer_set_list_style {name, ordered, bullet_char, indent_mm}` that creates/edits the named list
  style. Found 2026-09-18 building the proposal template.
