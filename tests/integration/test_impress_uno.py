# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LIVE test: the impress_* MCP tools drive a real LibreOffice Impress over UNO.

Run via the shared harness (starts an ISOLATED headless office and forces the
socket so it never touches the user's real session):

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_impress_uno.py -Port 2003

No API key needed. Exits non-zero on any failure.
"""

import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "mcp"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

import libreoffice_mcp as server  # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    print("Connected; blank presentation open")
    try:
        # --- Task 2: add_slide + overview -----------------------------------
        before = server.tool_impress_overview({})["count"]
        r = server.tool_impress_add_slide({"layout": "title_content"})
        _assert(r["count"] == before + 1, "count after add: %r" % r)
        _assert(r["layout"] == "title_content", "layout echoed: %r" % r)
        ov = server.tool_impress_overview({})
        _assert(ov["count"] == before + 1, "overview count: %r" % ov)
        _assert(ov["slides"][-1]["layout"] == "title_content",
                "last slide layout: %r" % ov["slides"][-1])
        _assert(ov["slides"][-1]["index"] == before + 1, "1-based index: %r" % ov)
        print("PASS: impress_add_slide + impress_overview")

        # insert a blank slide AFTER slide 1 -> it becomes slide 2
        n = server.tool_impress_overview({})["count"]
        r2 = server.tool_impress_add_slide({"after": 1, "layout": "blank"})
        _assert(r2["slide"] == 2, "new slide number after=1: %r" % r2)
        ov = server.tool_impress_overview({})
        _assert(ov["count"] == n + 1, "insert-after count: %r" % ov)
        _assert(ov["slides"][1]["layout"] == "blank",
                "inserted slide landed at position 2: %r" % ov["slides"][1])
        print("PASS: impress_add_slide after a 1-based slide")

        print("\nALL IMPRESS TOOL CHECKS PASSED")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
