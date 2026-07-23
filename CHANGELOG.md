# Changelog

All notable changes to the LibreOffice-Claude-Connector MCP server are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.9.0] — 2026-07-23

Writer toolset expansion: **137 → 154 tools** (+17 new, +3 extended), driven by
field reports from real Arabic/RTL Writer proposal sessions (see
`docs/KNOWN-GAPS.md`, Sessions 3/5/6).

### Added
- Paragraph structure & RTL: `writer_set_text_direction`, `writer_delete_paragraphs`,
  `writer_move_paragraphs`
- Tables: `writer_sort_table`, `writer_convert_table`, `writer_table_formula`,
  `writer_split_cells`, `writer_repeat_heading_rows`
- Styles & formatting: `writer_change_case`, `writer_apply_style`,
  `writer_clear_formatting`
- Captions & numbering: `writer_set_chapter_numbering`, `writer_insert_caption`,
  `writer_set_line_numbering`
- Reliability & multimedia: `set_active_document`, `writer_replace_image`,
  `form_control`

### Changed
- `writer_format_paragraph` — now targets by `start`/`count` index in addition to `search`
- `writer_edit_table` — now sets a cell's text after insert
- `set_style` — now sets `follow_style` (next-paragraph style)
- Bumped `SERVER_VERSION` and the `.mcpb` manifest to `0.9.0`

### Fixed
- Closed the focus-stealing hazard (`set_active_document`) where a background
  document grabbing focus silently redirected writes to the wrong document

### Tests
- Extended `tests/integration/test_mcp_tools_extended.py` with
  `check_writer_paragraph_ops`, `check_menu_coverage_tools`, `check_structural_tools`,
  `check_niche_tools`, `check_doc_activation_tools`, all verified against a real
  headless LibreOffice instance

## [0.8.0]

Completed the TOOLS-WANTED roadmap: **61 → 137 tools**, covering the bulk of the
Calc and Writer surface (sheet/document lifecycle, formatting, shapes, macros,
form controls, validation, and the initial Writer table/paragraph toolset).
