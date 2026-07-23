# Cross-agent use & portability

The MCP server is a plain **stdio JSON-RPC MCP server** with nothing
Claude-specific in the protocol layer, so any MCP-capable client can drive it —
not just Claude Desktop / Claude Code. Verified portability traits:

- `initialize` echoes the client's requested `protocolVersion` (falls back to
  `2024-11-05`), so newer/stricter clients aren't rejected.
- Declares only the standard `tools` capability — no sampling or bespoke methods.
- Standard library only, lazy `uno` import (`tools/list` works with no office
  running), and stdout kept clean (logs → stderr).

The `.mcpb` bundle is **Claude Desktop's** packaging format — other agents don't
consume it. Point them at the server command directly instead.

## Starting-point config (for testing)

The one thing every client needs identical: the `command` must be an
interpreter that has the `uno` module — i.e. **LibreOffice's bundled Python**
(`.../LibreOffice/program/python.exe` on Windows; the equivalent under
`soffice`'s program dir on macOS/Linux) — plus a reachable LibreOffice (the
agent-acceptor `.oxt` pipe, or the server auto-launches a socket instance).

**Codex CLI** — `~/.codex/config.toml`:
```toml
[mcp_servers.libreoffice]
command = "C:/Program Files/LibreOffice/program/python.exe"
args = ["<repo>/mcp/libreoffice_mcp.py"]
```

**Antigravity / Cursor / Windsurf / VS Code** — the generic `mcpServers` shape:
```json
{ "mcpServers": { "libreoffice": {
  "command": "C:/Program Files/LibreOffice/program/python.exe",
  "args": ["<repo>/mcp/libreoffice_mcp.py"]
}}}
```

## Opportunities for expansion

> **Not built yet — on purpose.** These are gated on real testing across
> **macOS, Linux, and non-Claude agents (Codex, Antigravity, Cursor, …)**. The
> plan is: get it running on those, gather feedback, then expand these (or add
> new ones) based on what actually breaks — rather than guessing now.

1. **Per-client config recipes.** Grow the snippets above into a tested matrix
   (Codex, Antigravity, Cursor, Windsurf, VS Code, Zed, …), each verified end to
   end, with the exact config-file location and any per-client quirks.
2. **macOS / Linux `uno`-Python discovery.** Confirm the LibreOffice bundled-
   Python path and auto-detection on each OS (framework layout on macOS, distro
   packaging on Linux) and document the working `command` per platform.
3. **Layout-independent launcher.** `mcpb/index.js` currently assumes the
   extracted-bundle layout, so it can't be pointed at straight from the repo.
   A `--server`/env override (or a small cross-platform wrapper) would let any
   client reuse the auto-detecting launcher instead of hard-coding the Python path.
4. **Optional HTTP/SSE transport.** stdio already covers Codex, Antigravity, and
   Claude; add a remote transport only if a target client requires it.
5. **Two-content-block result check.** `tools/call` returns two content blocks
   (a human summary line + the JSON payload). It's valid MCP, but verify each
   non-Claude client renders/parses a 2-block array cleanly; collapse to one
   block behind a flag if any client mishandles it.
6. **Per-agent capability notes.** Capture real-world differences as they surface
   — tool-count/name-length limits, result rendering, env-var handling, whether
   the client tolerates the server auto-launching LibreOffice — so onboarding a
   new agent becomes copy-paste.

Contributions welcome: test on your platform/agent, then open a PR extending the
recipe matrix or the launcher, or file what broke.
