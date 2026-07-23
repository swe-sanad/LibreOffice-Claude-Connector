# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Stamp ONE version string across every place a release version lives, so the
MCP server, the .mcpb bundle, and the .oxt extension all report the same number:

    python scripts/stamp_version.py 0.10.0

Rewrites in place (preserving formatting):
  * mcp/libreoffice_mcp.py   SERVER_VERSION = "..."
  * mcpb/manifest.json       top-level "version": "..."
  * ext/description.xml      <version value="..."/>

Idempotent; prints what changed. The release CI runs this with the git tag
version so the built artifacts always match the tag; run it yourself before
tagging (or before a local build meant to mirror a release)."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VER = re.compile(r"^\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?$")


def _sub(rel_path, pattern, repl, label):
    full = os.path.join(ROOT, rel_path)
    with open(full, encoding="utf-8") as fh:
        text = fh.read()
    new, n = re.subn(pattern, repl, text, count=1)
    if n == 0:
        raise SystemExit("stamp error: no %s found in %s" % (label, rel_path))
    changed = new != text
    if changed:
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
    print("  %-24s %s" % (rel_path, "updated" if changed else "unchanged"))


def main(version):
    if not _VER.match(version):
        raise SystemExit("version must look like 1.2.3, got: %r" % version)
    print("stamping version %s" % version)
    _sub("mcp/libreoffice_mcp.py",
         r'SERVER_VERSION\s*=\s*"[^"]*"',
         'SERVER_VERSION = "%s"' % version, "SERVER_VERSION")
    _sub("mcpb/manifest.json",
         r'("version"\s*:\s*)"[^"]*"',
         r'\g<1>"%s"' % version, '"version" key')
    _sub("ext/description.xml",
         r'(<version\s+value=)"[^"]*"',
         r'\g<1>"%s"' % version, "<version> element")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/stamp_version.py X.Y.Z")
    main(sys.argv[1])
