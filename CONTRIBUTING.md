<!-- SPDX-License-Identifier: MIT -->
# Contributing

Thanks for wanting to help! This project bridges **Claude** and **LibreOffice** —
an MCP server (61 tools and counting) plus an in-app `.oxt` extension. It's MIT
licensed, standard-library-only, and welcomes contributions of every size.

## Ways to contribute

- **Add an MCP tool** — the biggest, most welcome contribution. There's a ready
  pick-list of 85 proposed tools in [docs/TOOLS-WANTED.md](docs/TOOLS-WANTED.md),
  each mapped to the exact UNO API to wrap. Look for the 🥇 **good first tools**.
- **Fix a bug** or sharpen an error message / edge case.
- **Improve docs** — the README, a tool description, a worked example.
- **Test on macOS / Linux** — most testing so far is Windows; cross-platform
  reports and fixes are valuable (`lo_screenshot` is Windows-only today).
- **Help ship Rung 1** — a GUI toggle for the agent-acceptor (instead of the
  `CLAUDE_AGENT_ACCEPTOR` env var) and the extensions.libreoffice.org listing.
  See [docs/UPSTREAMING.md](docs/UPSTREAMING.md).

Open an issue before starting something big (or claiming a tool) so we don't
double-build.

## Ground rules

- **License:** MIT. Put `# SPDX-License-Identifier: MIT` at the top of new files.
- **Standard library only** for the MCP server and the Claude client — they run
  under LibreOffice's **bundled Python**, which has no `pip`. No third-party deps.
- **Lazy UNO:** never `import uno` at module top level in the server; import it
  inside the function (so `tools/list` works with no office running).
- **Naming:** tools are `snake_case` with a `calc_` / `writer_` prefix, or a
  neutral name for cross-cutting ones. Descriptions in `TOOL_DEFS` are **one line**.
- **Errors** should name the exception type and, for sheet-resolution failures,
  list the real sheet names (see `_resolve_sheet`). The `sheet` param already
  accepts an index, an exact name, or the English token of a bilingual
  `english | عربي` tab — reuse `_resolve_sheet`, don't re-implement.

## Dev setup

Everything runs under LibreOffice's own interpreter (no venv, no pip):

```
Windows:  C:\Program Files\LibreOffice\program\python.exe
macOS:    /Applications/LibreOffice.app/Contents/Resources/python
Linux:    /usr/bin/python3   (needs the python3-uno package)
```

Confirm it works:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" --version
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the deeper workflow (the
"LibreOffice caches Python modules" gotcha, isolated-profile testing, etc.).

## Add an MCP tool — the recipe

All server tools live in one file: [`mcp/libreoffice_mcp.py`](mcp/libreoffice_mcp.py).
Adding one is four small, local edits. Worked example — a `calc_recalculate` tool:

**1. Write the handler.** A function taking one `args` dict, returning a
JSON-serializable dict. Import `uno`/UNO types lazily.

```python
def tool_calc_recalculate(args):
    doc = _require_calc()
    hard = bool(args.get("hard", True))
    if hard:
        doc.calculateAll()
    else:
        doc.calculate()
    return {"recalculated": "all" if hard else "dirty"}
```

Helpers you'll reuse: `_require_calc()` / `_require_writer()` (assert doc type),
`_current_doc()`, `_resolve_sheet(doc, args.get("sheet"))`, `_hex_color(...)`,
`_uno_enum(type, value)`, `_uno_struct(type)`, `_pv(name, value)`.

**2. Register it in the `TOOLS` dict:**

```python
    "calc_recalculate": tool_calc_recalculate,
```

**3. Declare its schema in `TOOL_DEFS`** (drives `tools/list`; the description is
one line). Reuse the `_schema(props, required)` helper and the shared `_RANGE`,
`_SHEET`, `_BOOL`, `_INT`, `_STR`, `_NUM`, `_GRID` fragments:

```python
    {"name": "calc_recalculate",
     "description": "Force a recalculation of the spreadsheet (hard = full).",
     "inputSchema": _schema({"hard": dict(_BOOL, description="full recalc (default true)")})},
```

**4. Regenerate the tool reference:**

```powershell
python scripts/gen_mcp_tools_doc.py     # rewrites docs/MCP-TOOLS.md
```

If your tool imports a **new** module from `src/`, add that file to the bundle in
[`scripts/build_mcpb.py`](scripts/build_mcpb.py) (the `.mcpb` must ship every
module the import chain needs — a handshake can succeed while the first live tool
call dies on a missing module).

That's it — `git commit` includes the four edits **plus** the regenerated
`docs/MCP-TOOLS.md`.

## Test it

We practice **test-first** where practical: write the check, watch it fail for
the right reason, then make it pass. There are four test surfaces — use what fits:

1. **Offline unit tests** (`tests/test_*.py`) — for pure logic (parsing,
   coercion). No office, no network:
   ```powershell
   & "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
   ```

2. **UNO integration tests** (`tests/integration/*.py`) — drive a **real isolated**
   headless LibreOffice. This is where a new tool proves it works end to end. Add
   a case to `test_mcp_tools.py` / `test_mcp_tools_extended.py` and run:
   ```powershell
   powershell -File scripts/run_integration.ps1 -Test tests/integration/test_mcp_tools_extended.py
   ```
   The harness builds an isolated profile so it never touches your real office.

3. **Bundle simulation** (`scripts/test_mcpb_bundle.py --live`) — extracts the
   built `.mcpb` and drives it exactly like Claude Desktop (Node launcher, empty
   config). Run before any release; it catches "works in dev, broken in the
   bundle" issues.

4. **Acceptor test** (`scripts/run_acceptor_test.ps1`) — only if you touch the
   `.oxt` / the pipe path. Installs the extension into an isolated profile and
   connects flag-free over the named pipe.

A good tool PR adds at least one integration case that would fail without your
change.

## Pull requests

1. Fork, branch from `master` (`feat/calc-sort`, `fix/pipe-timeout`, …).
2. Make the change + its test; regenerate `docs/MCP-TOOLS.md` if you touched tools.
3. Run the relevant test surface(s) above and note the result in the PR.
4. Keep commits focused; use a clear message (Conventional-Commit style is nice
   but not required).
5. Open the PR against `master`. Describe **what** and **why**, and which tests you ran.

Questions? Open an issue. See also
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (design),
[docs/MCP-TOOLS.md](docs/MCP-TOOLS.md) (current tools), and
[docs/SECURITY.md](docs/SECURITY.md) (what an agent can do once connected).
