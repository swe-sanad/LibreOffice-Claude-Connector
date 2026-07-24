# TODO

## Upstream repo borrow backlog

### High priority

- [ ] Add a portmanteau-style dispatcher in [mcp/libreoffice_mcp.py](mcp/libreoffice_mcp.py) so agents can call a single entrypoint with operation-specific arguments.
- [ ] Add bridge/proxy support for extension-hosted LibreOffice MCP tools so this repo can talk to a local in-LO bridge as well as its own server.
- [ ] Add a lightweight local HTTP bridge for non-stdio integrations and easier desktop/web client usage.
- [ ] Add document conversion, merge, and batch-pack helpers that complement the existing live Calc/Writer editing tools.

### Medium priority

- [ ] Evaluate an OooDev-style abstraction layer for future helper code so we can reduce repetitive UNO boilerplate.
- [ ] Add richer Calc helpers for formatting, conditional formatting, charts, and pivot-style workflows.
- [ ] Improve packaging/distribution ergonomics for MCPB and VS Code/Copilot integration.

### Comparison findings from upstream repos

- [ ] From sandraschi/libreoffice-mcp: capture the operation-dispatch pattern around status, convert, convert_batch, document_info, merge, list_templates, batch_pack, pdf_merge, watch_start/watch_stop/watch_status, live_write/live_type, run_macro/run_python_macro/list_macros, bridge_discover/bridge_call, read_spreadsheet, and help.
- [ ] From patrup/mcp-libre: capture the lightweight live-extension tool set for create_document_live, insert_text_live, get_document_info_live, format_text_live, save_document_live, export_document_live, get_text_content_live, and list_open_documents.
- [ ] From waterpistolai/libreoffice-mcp: capture the richer Calc/Writer/Base helper set for open_document, new_document, save_document, close_document, get_sheet_names, get_cell_value, set_cell_value, create_new_sheet, create_pivot_table, sort_range, calculate_statistics, run_query, list_tables, create_table, insert_data, create_form, create_report, insert_text, apply_style, run_macro, insert_form_control, format_cell_range, conditional_format, and create_chart.
- [ ] Adopt a hybrid approach: keep our current live UNO server as the core, but borrow the best ideas from each upstream repo instead of copying one wholesale.

### Validation

- [ ] Add regression tests for each new capability before merging.
- [ ] Re-run the live Calc/Writer MCP verification flow after implementation.
