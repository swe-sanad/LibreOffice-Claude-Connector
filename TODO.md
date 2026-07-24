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

### Genuine gaps — opportunities (prioritized)

> Gated on real cross-platform (macOS/Linux) + cross-agent testing — build from
> feedback, not speculation. Regression-test each before merging.

**P1 — new capability, clear value**
- [ ] **LibreOffice Base (database) support** — a whole app we don't cover:
  `run_query`, `list_tables`, `create_table`, `insert_data`, `create_report`,
  `create_form` (waterpistolai). Scope a `base_*` tool family.
- [ ] **Headless conversion** — `convert` / `convert_batch` (any→any via a
  headless soffice/UNO filter), beyond today's save/export to a few formats.
- [ ] **Merge** — document merge + `pdf_merge` (+ `batch_pack`) (sandraschi).

**P2 — useful**
- [ ] **Templates** — `list_templates` + create-from-template.
- [ ] **Python macros** — `run_python_macro` + a general `list_macros`
  (we have Basic only, via `run_macro` / `basic_module`).
- [ ] **Impress / Draw** — the other unsupported apps (no upstream covers them
  either); a future frontier once Base lands.
- [ ] **Optional dispatcher facade (tool-count relief)** — 161 tools exceeds some
  clients' tool caps; an OPTIONAL portmanteau tool that fans out to the existing
  tools would help those agents WITHOUT replacing the discrete tools. Confirm the
  real per-client limits first (see `docs/CROSS-AGENT.md`).

**P3 — convenience / niche**
- [ ] `calculate_statistics` — one-shot descriptive stats on a range.
- [ ] `read_spreadsheet` — dump all sheets at once (today: per-sheet
  `calc_get_used_range`).
- [ ] `watch_start/stop/status` (document/file watching) and `live_type`
  (simulated typing for demos) — novelty, low value.
- [ ] `bridge_discover/bridge_call` — talk to other in-LO MCP bridges; our
  pipe-first `_connect` already reaches an extension-hosted office, so mostly moot.

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
