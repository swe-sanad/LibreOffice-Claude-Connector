# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LIVE test: the MCP server's tools drive a real LibreOffice over UNO.

Run via the shared harness (starts an isolated headless office):

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_mcp_tools.py

Exercises the tool functions directly (the JSON-RPC layer is covered separately
by mcp/test_mcp_protocol.py). No API key needed.
"""

import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "mcp"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

import libreoffice_mcp as server

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    os.environ["LO_UNO_PORT"] = str(PORT)
    desktop = server._desktop()          # connects to the running headless office
    print("Connected on port", PORT)

    doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, ())
    try:
        sheet = doc.getSheets().getByIndex(0)
        sheet.getCellRangeByName("A1:B2").setDataArray((("x", "y"), (1.0, 2.0)))

        # read via the tool
        read = server.tool_calc_read_range({"range": "A1:B2"})
        _assert(read["cells"] == [["x", "y"], [1.0, 2.0]], "read: %r" % read)
        print("PASS: calc_read_range")

        # write via the tool
        server.tool_calc_write_range({"range": "A1:B2",
                                      "cells": [["X", "Y"], [10, 20]]})
        again = server.tool_calc_read_range({"range": "A1:B2"})
        _assert(again["cells"] == [["X", "Y"], [10.0, 20.0]], "write: %r" % again)
        print("PASS: calc_write_range")

        # dimension mismatch is rejected
        try:
            server.tool_calc_write_range({"range": "A1:B2", "cells": [["only one"]]})
            raise AssertionError("expected a shape-mismatch error")
        except RuntimeError:
            print("PASS: calc_write_range rejects a shape mismatch")

        # status lists the open Calc doc
        status = server.tool_lo_status({})
        _assert(status["connected"], "not connected: %r" % status)
        _assert(any(d["type"] == "calc" for d in status["documents"]),
                "no calc doc listed: %r" % status)
        print("PASS: lo_status lists the open Calc document")

        print("\nALL MCP TOOL CHECKS PASSED (server drives real LibreOffice)")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
