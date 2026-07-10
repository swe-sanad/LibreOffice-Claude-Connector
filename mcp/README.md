# LibreOffice MCP server

The **inverse** of the `.oxt` extension. Instead of embedding Claude *inside*
LibreOffice, this lets an external MCP client — **Claude Code**, Claude Desktop,
or Cowork — reach *into* a running LibreOffice and drive it as a tool, the same
way Claude uses the Figma or Chrome MCP servers.

- `libreoffice_mcp.py` — the server. Standard-library only (implements MCP's
  JSON-RPC-over-stdio by hand), runs under LibreOffice's **bundled Python** so it
  has the `uno` module, and reuses the proven UNO helpers in `../src/uno_bridge.py`.
- `test_mcp_protocol.py` — protocol smoke test (no LibreOffice needed).
- Live tool test: `../tests/integration/test_mcp_tools.py`.

## Tools

| Tool | What it does |
|---|---|
| `lo_status` / `list_documents` | Check the connection; list open documents |
| `get_current_selection` | The active selection — a Calc range (with data) or selected Writer text |
| `calc_read_range` | Read a Calc range (`A1:C10`) as a 2-D array |
| `calc_write_range` | Write a 2-D array into a Calc range (dimensions must match) |
| `writer_get_text` | Full body text of the active Writer document |
| `writer_replace_selection` | Replace the Writer selection (or insert at the caret) |

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
