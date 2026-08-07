# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Regenerate docs/MCP-TOOLS.md from the live tool registry.

    "C:/Program Files/LibreOffice/program/python.exe" scripts/gen_mcp_tools_doc.py

This used to scrape `TOOL_DEFS = [...]` out of one big file with a regex. The
server is a package now, so it imports the registry instead — which is both
simpler and honest: the document then describes what the server ACTUALLY
registers, not what the source happens to look like. It does need LibreOffice's
Python, because importing the package pulls in the modules (still no running
office: `uno` itself is imported lazily).

One section per tool module, so the document mirrors the code layout.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "MCP-TOOLS.md")
sys.path.insert(0, os.path.join(ROOT, "mcp"))

import libreoffice_mcp  # noqa: E402
from loconn import registry  # noqa: E402

TITLES = {
    "shared": "Cross-application", "calc": "Calc", "writer": "Writer",
}


def main():
    # group by the module each tool registered from
    sections = []
    for definition in registry.TOOL_DEFS:
        module = registry.TOOLS[definition["name"]].__module__.rsplit(".", 1)[-1]
        if not sections or sections[-1]["module"] != module:
            sections.append({"module": module, "tools": []})
        sections[-1]["tools"].append(definition)

    total = sum(len(s["tools"]) for s in sections)
    basic = len(registry.BASIC_TOOLS)
    out = [
        "# MCP tool reference", "",
        "All **%d tools** of the `libreoffice` MCP server (v%s), generated from the"
        % (total, libreoffice_mcp.SERVER_VERSION),
        "live registry by `scripts/gen_mcp_tools_doc.py`. Do not edit by hand.", "",
        "**%d** are advertised by default; the rest are reachable by name through"
        % basic,
        "`dispatch`. Set `LO_TOOLS=full` to advertise them all. A ✅ marks a tool in",
        "the everyday tier.", "",
        "One section per source module — the document mirrors `mcp/loconn/tools/`.",
        "",
    ]
    for section in sections:
        app, _, concern = section["module"].partition("_")
        title = TITLES.get(app, app.title())
        heading = "%s — %s" % (title, concern) if concern else title
        out += ["## %s" % heading, "",
                "| Tool | | Description |", "|---|---|---|"]
        for definition in section["tools"]:
            name = definition["name"]
            out.append("| `%s` | %s | %s |" % (
                name, "✅" if name in registry.BASIC_TOOLS else "",
                definition["description"].replace("|", "\\|")))
        out.append("")

    io.open(OUT, "w", encoding="utf-8", newline="").write("\n".join(out))
    print("wrote %s: %d tools (%d advertised) in %d sections"
          % (OUT, total, basic, len(sections)))


if __name__ == "__main__":
    main()
