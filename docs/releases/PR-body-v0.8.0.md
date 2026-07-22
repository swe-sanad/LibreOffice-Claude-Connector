<!-- Title: feat(mcp): complete the TOOLS-WANTED roadmap — 61 → 137 tools -->
# feat(mcp): complete the TOOLS-WANTED roadmap — 61 → 137 tools

## Summary

Lands the full `docs/TOOLS-WANTED.md` wish-list: 76 new MCP tools, taking the stdio
server from 61 to 137 tools. The roadmap was originally a prioritized list of 85
proposed tools from a 5-domain-expert gap analysis against the prior 61-tool surface;
after applying the doc's own "don't build two of these" umbrella guidance, 76 tools
were built and 9 overlapping proposals were consolidated away. `docs/MCP-TOOLS.md` was
regenerated (137 tools, 16 sections) and `docs/TOOLS-WANTED.md` / `docs/CHANGELOG.md`
updated to reflect shipped status. This is server-only — the `.oxt` extension version
is unaffected.

## What changed

- **Good-first (12):** single-API wrappers — sort/dimensions/visibility/move/
  recalculate/delete-comment/delete-chart for Calc; word_count/read_table/
  get_paragraphs for Writer; document properties get/set.
- **Writer (22):** objects (list/edit/delete), tables (edit), fields, TOC + index
  refresh, footnotes, bookmarks, cross-references, shapes/text frames, sections,
  track-changes, watermark, redaction, spellcheck, page background, list styling, mail
  merge.
- **Calc (29):** shapes/images, autofilter + standard filter, chart edit/list, named
  ranges, pivot create/refresh, subtotals, goal-seek, fill-series, cell protection,
  advanced formatting + read-back (cell format/conditional format/validation),
  page-setup/print-area, grouping (shapes + outline), what-if (multiple operations),
  dedupe/transpose, sparklines, scale-format (data bars/color scales/icon sets),
  copy-sheet.
- **Cross-cutting umbrellas (13):** `set_hyperlink`, `export_document`,
  `set_document_properties`, `list_styles`/`set_style`, `protect_document`,
  `dispatch_uno`, `document_undo`, `bind_document_event`, `set_view_zoom`,
  `get_signatures`, `list_embedded_objects`, `insert_ole_object`.

Full new-tool list and per-tool descriptions: `docs/MCP-TOOLS.md`, `docs/CHANGELOG.md`
(`[0.8.0]`), `docs/releases/v0.8.0.md`.

## Design decisions

- **Umbrella consolidation over one-tool-per-proposal.** Where `TOOLS-WANTED.md`
  flagged overlapping proposals, the consolidated tool was built and the narrower
  duplicates skipped: `calc_set_hyperlink`/`writer_insert_hyperlink` →
  `set_hyperlink`; `writer_export_pdf` → `export_document`; `calc_define_name` →
  `calc_named_ranges`; `writer_insert_bookmark` → `writer_bookmarks`;
  `writer_manage_styles` → `list_styles`+`set_style`; `calc_protect_sheet` →
  `protect_document`+`calc_cell_protection`; `refresh_fields` → covered by the already
  shipped `writer_update_indexes`+`calc_recalculate`; `writer_insert_ole_chart` →
  `insert_ole_object object=chart`. Reduces surface area and keeps one call path per
  concern instead of two near-duplicate tools that could silently drift apart.
- **Best-effort, fail-in-band for version-sensitive UNO APIs.** `calc_create_pivot`,
  `calc_add_scale_format`, `calc_add_sparkline`, `calc_multiple_operations`, and
  `writer_mail_merge` wrap UNO APIs whose shape varies across LibreOffice versions. Each
  is written to catch the mismatch and return a clear tool-level error rather than throw
  an uncaught exception that would crash the server process for every other in-flight
  tool call.

## Testing

Done this session (no running LibreOffice instance was available — it was in use by
another session):
- AST parse of `mcp/libreoffice_mcp.py`.
- Module import with no import-time errors.
- Office-free protocol smoke test: MCP handshake + `tools/list` confirming all 137
  tools register with valid schemas (tool bodies are not invoked without a running office).
- 137/137 `TOOLS` ↔ `TOOL_DEFS` consistency check (no dupes/orphans, all handlers
  callable, all JSON schemas valid).

**Gap — explicitly not done:** no live-UNO exercise against a real LibreOffice
instance. The 5 version-sensitive best-effort tools listed above have not been called
against live Calc/Writer documents and their happy paths are unverified beyond static
analysis.

## Reviewer notes

Highest-risk area to check before relying on this release: the **5 version-sensitive
best-effort tools** — `calc_create_pivot`, `calc_add_scale_format`,
`calc_add_sparkline`, `calc_multiple_operations`, `writer_mail_merge`. Recommend a
live smoke-test pass (open a real Calc/Writer doc, call each with representative
arguments) before treating them as production-ready; everything else in this PR was
only offline-validated (AST/import/schema-consistency), so a broader live pass over a
sample of the other 71 new tools would also reduce risk, but these 5 are the ones
whose UNO API shape is known to vary by LibreOffice version.
