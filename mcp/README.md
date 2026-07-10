# LibreOffice MCP server

The **inverse** of the `.oxt` extension. Instead of embedding Claude *inside*
LibreOffice, this lets an external MCP client — **Claude Code**, Claude Desktop,
or Cowork — reach *into* a running LibreOffice and drive it as a tool, the same
way Claude uses the Figma or Chrome MCP servers.

- `libreoffice_mcp.py` — the server. Standard-library only (implements MCP's
  JSON-RPC-over-stdio by hand), runs under LibreOffice's **bundled Python** so it
  has the `uno` module, and reuses the proven UNO helpers in `../src/uno_bridge.py`.
- `test_mcp_protocol.py` — protocol smoke test (no LibreOffice needed).
- Live tool tests: `../tests/integration/test_mcp_tools.py` (core) and
  `../tests/integration/test_mcp_tools_extended.py` (full 37-tool sweep).

## Tools (37)

**Status & selection**

| Tool | What it does |
|---|---|
| `lo_status` / `list_documents` | Check the connection; list open documents (title, type, url) |
| `get_current_selection` | The active selection — a Calc range (with data) or selected Writer text |

**Document lifecycle**

| Tool | What it does |
|---|---|
| `create_document` | New empty `calc` or `writer` document |
| `open_document` | Open a file (ods/xlsx/csv/odt/docx/…) |
| `save_document` | Save in place, save-as (ods/xlsx/csv/odt/docx/txt), or export a PDF copy |
| `close_document` | Close the active document (optionally saving first) |

**Calc — data**

| Tool | What it does |
|---|---|
| `calc_read_range` | Read a range (`A1:C10`) as a 2-D array of values |
| `calc_write_range` | Write a 2-D array into a range (dimensions must match) |
| `calc_get_formulas` / `calc_set_formulas` | Read/write formulas (`=SUM(A1:A3)`) instead of values |
| `calc_clear_range` | Clear contents (optionally formatting too) |
| `calc_copy_range` | Copy a range (values + formulas + formatting) to a target cell/sheet |
| `calc_find_replace` | Find & replace across one sheet or all sheets |
| `calc_get_used_range` | The used area of a sheet (A1 range, size, optionally the data) |
| `calc_insert_rows` / `calc_delete_rows` | Insert/delete rows at an index |
| `calc_insert_columns` / `calc_delete_columns` | Insert/delete columns at an index |

**Calc — sheets & presentation**

| Tool | What it does |
|---|---|
| `calc_list_sheets` / `calc_add_sheet` / `calc_delete_sheet` / `calc_rename_sheet` | Sheet management |
| `calc_format_range` | Bold/italic/underline, font, colors, wrap, alignment, number format, auto-fit |
| `calc_merge_cells` | Merge / unmerge a range |
| `calc_create_chart` | Embedded chart (column, bar, line, pie, area, scatter) |
| `calc_select_range` | Highlight a range in the GUI for the user |

**Writer**

| Tool | What it does |
|---|---|
| `writer_get_text` | Full body text of the active document |
| `writer_replace_selection` | Replace the selection (or insert at the caret) |
| `writer_append_text` | Append text at the end (`\n` = paragraph break) |
| `writer_insert_heading` | Append a Heading 1–6 paragraph |
| `writer_find_replace` | Find & replace across the document |
| `writer_format_text` | Apply character formatting to every match of a search |
| `writer_insert_table` | Insert a table, optionally pre-filled with data |
| `writer_insert_image` | Insert an image file (sized in mm) |
| `writer_insert_page_break` | Page break at the end |
| `writer_get_outline` | The document's headings as `[{level, text}]` |

Not yet covered: pivot tables (UNO DataPilot), Impress/Draw, conditional
formatting, comments/track-changes. Ask if you need one of these next.

## Use it from Claude Code

**1. Start LibreOffice with a UNO socket** (fully close it first — it's
single-instance, so a second launch won't open the socket):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_office_socket.ps1
# → LibreOffice opens listening on localhost:2002
```

**2. Register the server** with Claude Code (one command):

```bash
claude mcp add libreoffice --env LO_UNO_PORT=2002 -- \
  "C:/Program Files/LibreOffice/program/python.exe" \
  "E:/SWE-Pioneers/LibreOffice-Claude-Connector/mcp/libreoffice_mcp.py"
```

…or add a project-scoped `.mcp.json` in the repo root (Claude Code will ask you
to approve it on next start):

```json
{
  "mcpServers": {
    "libreoffice": {
      "command": "C:\\Program Files\\LibreOffice\\program\\python.exe",
      "args": ["E:\\SWE-Pioneers\\LibreOffice-Claude-Connector\\mcp\\libreoffice_mcp.py"],
      "env": { "LO_UNO_PORT": "2002" }
    }
  }
}
```

**3. Then just ask Claude Code** — e.g. "read A1:C20 of the open sheet and write
a summary into E1", or "replace my Writer selection with a formal version". The
server operates on whatever document is active in that LibreOffice session.

## Notes

- The server contacts LibreOffice lazily: `initialize`/`tools/list` work with no
  office running; a tool call fails cleanly until LibreOffice is up on the socket.
- `LO_UNO_PORT` (default `2002`) must match the port LibreOffice was started with.
- Runs under **LibreOffice's** `python.exe` (for `uno`) — not system Python.
- Claude Desktop / Cowork: no one-click directory entry exists, but they accept
  custom MCP servers via config; the same command/args apply.
