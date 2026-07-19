# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Regenerate docs/MCP-TOOLS.md from TOOL_DEFS in mcp/libreoffice_mcp.py.

Parses the source (no `uno` runtime needed), so it runs under any Python 3.8+:

    python scripts/gen_mcp_tools_doc.py
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "mcp", "libreoffice_mcp.py")
OUT = os.path.join(ROOT, "docs", "MCP-TOOLS.md")


def main():
    src = io.open(SRC, encoding="utf-8").read()
    version = re.search(r'SERVER_VERSION = "([^"]+)"', src).group(1)
    body = re.search(r"TOOL_DEFS = \[(.*?)\n\]", src, re.S).group(1)

    sections = []
    cur = None
    pattern = (r'# --- (.+?) ---'
               r'|\{"name": "([a-z_]+)",\s*"description": (".*?"),\s*\n?\s*"inputSchema"')
    for m in re.finditer(pattern, body, re.S):
        if m.group(1):
            cur = {"title": m.group(1), "tools": []}
            sections.append(cur)
        else:
            desc = json.loads(re.sub(r'"\s*\n\s*"', "", m.group(3)))
            if cur is None:
                cur = {"title": "misc", "tools": []}
                sections.append(cur)
            cur["tools"].append((m.group(2), desc))

    total = sum(len(s["tools"]) for s in sections)
    out = ["# MCP tool reference", "",
           "All **%d tools** of the `libreoffice` MCP server (v%s), generated from"
           % (total, version),
           "`mcp/libreoffice_mcp.py`'s `TOOL_DEFS`. Regenerate with the snippet in",
           "`docs/DEVELOPMENT.md` after adding tools.", ""]
    for s in sections:
        out += ["## %s" % s["title"].capitalize(), "",
                "| Tool | Description |", "|---|---|"]
        out += ["| `%s` | %s |" % (n, d.replace("|", "\\|")) for n, d in s["tools"]]
        out.append("")
    io.open(OUT, "w", encoding="utf-8", newline="").write("\n".join(out))
    print("wrote %s: %d tools in %d sections" % (OUT, total, len(sections)))


if __name__ == "__main__":
    main()
