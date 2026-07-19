# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Assemble dist/libreoffice-connector-<version>.mcpb (a Claude Desktop MCP
Bundle — a zip with manifest.json at the root). Stdlib only:

    python scripts/build_mcpb.py

The bundle ships the MCP server + its uno_bridge dependency and points the
runtime at the USER'S LibreOffice bundled Python via user_config (the server
needs LibreOffice's `uno` module, which no bundled runtime can provide).
"""
import io
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    manifest_path = os.path.join(ROOT, "mcpb", "manifest.json")
    manifest = json.load(io.open(manifest_path, encoding="utf-8"))
    version = manifest["version"]
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "libreoffice-connector-%s.mcpb" % version)

    files = [
        (manifest_path, "manifest.json"),
        (os.path.join(ROOT, "mcpb", "index.js"), "index.js"),
        (os.path.join(ROOT, "mcp", "libreoffice_mcp.py"), "mcp/libreoffice_mcp.py"),
        (os.path.join(ROOT, "src", "uno_bridge.py"), "src/uno_bridge.py"),
        (os.path.join(ROOT, "ext", "icons", "icon.png"), "icon.png"),
        (os.path.join(ROOT, "LICENSE"), "LICENSE"),
        (os.path.join(ROOT, "docs", "MCP-TOOLS.md"), "docs/MCP-TOOLS.md"),
    ]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in files:
            z.write(src, arc)
    size = os.path.getsize(out)
    print("wrote %s (%.1f KB, %d files)" % (out, size / 1024.0, len(files)))


if __name__ == "__main__":
    main()
