# Upstream parity — pruning the sibling LibreOffice-MCP projects

Goal: **absorb the useful capabilities of the sibling LibreOffice-MCP projects
into this one, and supersede them.** This tracks what's been pulled in, what was
already covered, and what's intentionally left for a later (or someone else's)
session.

Sources surveyed: `sandraschi/libreoffice-mcp`, `patrup/mcp-libre`,
`waterpistolai/libreoffice-mcp`.

## Pruned in — shipped (v0.9.2)

Nine tools that fit our Writer/Calc/cross-app model:

| Tool | What it does | Borrowed from |
|------|--------------|---------------|
| `convert` | Headless format conversion of one or many files (docx/xlsx→pdf, odt→docx, …) | sandraschi (`convert`/`convert_batch`) |
| `merge` | Merge Writer/text documents into one, page-break between | sandraschi (`merge`) |
| `dispatch` | Portmanteau facade — run any tool by name, or list the catalog | sandraschi (operation-dispatch pattern) |
| `list_templates` | List templates under the configured Template paths | sandraschi (`list_templates`) |
| `create_from_template` | New untitled doc from a template file | templates |
| `run_python_macro` | Invoke a Python macro (complements Basic `run_macro`) | sandraschi (`run_python_macro`) |
| `list_macros` | Discover document Basic modules + user Python scripts | sandraschi (`list_macros`) |
| `calc_statistics` | count/sum/mean/min/max/median/stdev over a range | waterpistolai (`calculate_statistics`) |
| `read_spreadsheet` | Dump every sheet's used range in one call | sandraschi (`read_spreadsheet`) |

The `dispatch` facade also answers the "portmanteau dispatcher" idea from
sandraschi **without** collapsing our discrete tools — clients with a tool-count
cap can drive all 170 tools through the one `dispatch` entry.

## Already covered before this pass — no action

- **patrup/mcp-libre** — the entire `*_live` set maps 1:1 to tools we already
  had (`create_document`, `writer_append_text`, `writer_format_text`,
  `save_document`, `export_document`, `get_document_properties`,
  `writer_get_text`, `list_documents`).
- **waterpistolai Calc/Writer core** — `get/set_cell_value`, `create_pivot_table`,
  `sort_range`, `create_chart`, `conditional_format`, `format_cell_range`,
  `apply_style`, `insert_form_control`, `run_macro`, sheet lifecycle, … all
  pre-existing.
- **sandraschi** — `document_info` → `get_document_properties`, `status` →
  `lo_status`, `help` → MCP `tools/list`, `run_macro` → `run_macro`/`basic_module`.

## Deferred — for a later / far-away session (or contributors)

Left out on purpose; each is either a **new LibreOffice app** we don't model yet,
or breaks a project rule, or is low-value. Contributions welcome.

| Item | Source | Why deferred |
|------|--------|--------------|
| **Base (database)**: `run_query`, `list_tables`, `create_table`, `insert_data`, `create_report`, `create_form` | waterpistolai | A whole new **app** (LibreOffice Base). Needs its own `base_*` family + a DB connection model — bigger than a tool add. **Highest-value next frontier.** |
| **Impress / Draw** | — (none of the three cover them) | New apps; no upstream to borrow from. Future. |
| `pdf_merge` | sandraschi | Real PDF merging needs a PDF library — breaks the **stdlib-only** rule. `merge` covers text docs; revisit if a bundled dependency is ever allowed. |
| `batch_pack` | sandraschi | Package outputs into an archive — niche, unclear demand. `convert` already handles batch conversion. |
| `watch_start/stop/status` | sandraschi | Live document/file watching — novelty; no clear MCP use yet. |
| `live_write/live_type` | sandraschi | Simulated per-character typing (for screencasts) — cosmetic. |
| `bridge_discover/bridge_call` | sandraschi | Reach *other* in-LO MCP bridges — largely moot: our pipe-first `_connect` already reaches an extension-hosted office. |

## Status

- Superseded outright: **patrup** (fully), **waterpistolai** (except Base),
  **sandraschi** (except the deferred niche/PDF/bridge items).
- Remaining to close the gap and supersede all three entirely: **Base support**
  (the big one), then Impress/Draw as new frontiers.
