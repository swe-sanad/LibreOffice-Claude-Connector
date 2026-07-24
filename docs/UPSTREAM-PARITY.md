# Upstream parity — pruning the sibling LibreOffice-MCP projects

Goal: **absorb the useful capabilities of the sibling LibreOffice-MCP projects
into this one, and supersede them.** This tracks what's been pulled in, what was
already covered, and what's intentionally left for a later (or someone else's)
session.

Sources surveyed: `sandraschi/libreoffice-mcp`, `patrup/mcp-libre`,
`waterpistolai/libreoffice-mcp`, `quazardous/nelson-mcp` (HTTP-based, 100+ tools,
Writer/Calc/Draw/Impress), and `KeithCu/writeragent` (HTTP extension with a deep
data-science/quant layer). Each has its own section below.

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

---

## Nelson MCP — `quazardous/nelson-mcp` (the ambitious one)

The most advanced sibling: **100+ tools** across **Writer / Calc / Draw / Impress**,
and architecturally different — it embeds an **HTTP MCP server inside LibreOffice**
(the extension *is* the server, at `http://localhost:8766/mcp`), whereas we run an
**external stdio server** that drives LibreOffice over UNO (+ an optional `.oxt`
for the agent-acceptor pipe and the Claude menu). Superseding it is a bigger lift
than the other three; triaged below.

### Already comparable
- Tool breadth: our **170 tools** vs its "100+" — text/paragraphs/styles, tables,
  charts, conditional formatting, hyperlinks, images/shapes, bookmarks/comments/
  search, file lifecycle + PDF export, and batch are all covered on both sides.

### Genuine gaps — adopt candidates (prioritized)

**P1 — closes the architecture/ergonomics gap**
- [ ] **HTTP (Streamable-HTTP/SSE) transport** — Nelson's core selling point;
  already on our list (`docs/CROSS-AGENT.md`). This is now the single most
  impactful item for parity + remote clients.
- [ ] **Persistent document IDs + per-call `_document` targeting** — a UUID stored
  in the file (survives save/close/reopen); every tool accepts `_document`
  (`id:`/`path:`/`title:`) to act on any open doc, not just the focused one.
  A more robust multi-doc model than our focus + `set_active_document`.
- [ ] **Structured errors** — `{code, message, hint, retryable}` (e.g.
  `unsaved_document`, `incompatible_doc_type`, `execution_timeout`) + "did-you-mean"
  enum suggestions (Levenshtein). We currently raise typed-but-unstructured errors.
- [ ] **Tool presets / custom endpoints** — expose only a named subset
  (minimal / writer-edit / calc / …) to reduce tool confusion on smaller LLMs.
  Pairs directly with our `dispatch` facade and the 170-tool count concern.

**P2 — useful**
- [ ] **Undo-wrapped mutations** — wrap each tool op in an UndoContext so one
  Ctrl+Z reverts the whole operation (we expose `document_undo`, but don't group).
- [ ] **Draw / Impress** — Nelson covers them; reinforces our deferred frontier.
- [ ] **Response context** — include `_resolved` (doc id/type/title) + `_session`
  in every result; a `/health` bootstrap endpoint (pairs with HTTP mode).
- [ ] **Batch variable-chaining** — thread outputs between steps (our `batch`
  runs steps but doesn't chain variables).
- [ ] **One-click client launchers** — register the server into Claude Code /
  Gemini CLI / OpenCode / Goose from inside LibreOffice (ties to our per-client
  config recipes in `docs/CROSS-AGENT.md`).
- [ ] **Calc `=PROMPT()`** — call an LLM from a cell (an `.oxt`-side feature,
  sibling to our existing Claude-menu commands).

**P3 / out of core scope**
- [ ] Tunnels (ngrok/Cloudflare/bore/Tailscale) + auto-SSL — remote-access ops;
  relevant only with HTTP mode, and arguably deployment-layer, not MCP-core.
- [ ] AI **image generation** (Stable Diffusion / OpenAI / AI Horde) + AI image
  indexation — an AI-content feature, not a document-automation primitive; belongs
  (if anywhere) to the `.oxt` AI side, not the MCP server.

---

## WriterAgent — `KeithCu/writeragent` (the feature-dense one)

A Python LibreOffice extension that also runs a local **HTTP MCP server**
(default `:8765/mcp`, + stdio for agent backends), across **Writer / Calc / Draw /
Impress**. Its standout is a deep **data-science / quant / symbolic-math** layer —
which is exactly where it collides with our **stdlib-only** rule.

### Already comparable
- Core Writer/Calc document tools, conditional formatting, AutoFilter, batch, and
  basic descriptive stats (`calc_statistics`) are covered on both sides.

### Adopt candidates that FIT our model
- [ ] **HTTP transport** — same conclusion as Nelson; the top item (see above).
- [ ] **Per-request document targeting** — its `document_url` param /
  `X-Document-URL` header ≈ Nelson's `_document`; adopt once (one multi-doc model).
- [ ] **MathML / LaTeX → LibreOffice Math object** insertion — doable via UNO with
  no third-party lib (the *symbolic* side below is not).
- [ ] **Format-preserving ("surgical") replace** — keep bold/italic/size across a
  text replacement; an upgrade to `writer_find_replace`.
- [ ] **Undo-wrapped rewrites** — same item as Nelson's undo support.

### Out of scope — needs third-party libraries (breaks stdlib-only)
> The strategic catch: WriterAgent's differentiators mostly require
> NumPy/pandas/SciPy/SymPy/matplotlib/DuckDB/embeddings — none installable in
> LibreOffice's bundled Python without `pip`. Pursue **only** if the project ever
> relaxes the stdlib-only constraint (bundled venv / optional deps).
- Calc DS/quant suite: `=PY()`/`=PYTHON()`, `describe_data`/`kpi_summary`/
  `detect_outliers`/`pivot_aggregate`/`correlation_matrix`/`run_regression`/
  `cluster_numeric`/`monte_carlo`, `quick_plot`/`correlation_heatmap`/
  `time_series_plot`, `technical_analysis`/`portfolio_tearsheet`/
  `efficient_frontier`/`optimize_portfolio`/`linear_programming`, unit conversion,
  DuckDB `query_folder_sql`, spreadsheet→Python (235+ functions).
- SymPy symbolic math (`solve_equation`/`integrate`/`differentiate`).
- Semantic cross-file search (BM25 + embeddings), OCR (Docling), web search,
  audio capture, image generation, grammar backends (LanguageTool/Harper).
- These overlap Nelson's AI-content features and sit outside a stdlib-only
  document-automation MCP's core. `calc_statistics` already covers the *basic*
  stats without any dependency.

## Status

- Superseded outright: **patrup** (fully), **waterpistolai** (except Base),
  **sandraschi** (except the deferred niche/PDF/bridge items).
- Remaining to fully supersede all five: **HTTP transport** and **Base support**
  are the two highest-leverage items; then Impress/Draw, structured errors +
  per-call `_document` targeting + tool presets to match Nelson/WriterAgent agent
  ergonomics.
- **Strategic fork:** Nelson's AI-image/tunnels and WriterAgent's data-science/
  quant/symbolic layer are the features we can't match under **stdlib-only** (they
  need NumPy/SciPy/SymPy/embeddings/etc.). Superseding those means a deliberate
  decision to bundle third-party deps (a venv/optional-deps story) — otherwise we
  compete on breadth (170 tools), portability (any stdio MCP client + HTTP once
  added), zero-dependency install, and the cross-agent story, and cede the
  heavy-AI/DS niche. Recommend: land HTTP + Base + the ergonomics items first;
  treat the DS/AI layer as a separate, explicit product decision.
