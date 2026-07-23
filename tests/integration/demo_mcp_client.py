# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Demo: act as an MCP CLIENT and drive real LibreOffice through the server's
JSON-RPC `tools/call` protocol — the exact path Claude Code would use.

Run via the harness (which starts a headless office on the socket):

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/demo_mcp_client.py
"""

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))
SERVER = os.path.join(_HERE, "..", "..", "mcp", "libreoffice_mcp.py")

import uno_bridge as ub

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))


def main():
    # 1. Open a spreadsheet in the running office so the server has a live doc.
    ctx, smgr, desktop = ub.connect(port=PORT)
    doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, ())
    sheet = doc.getSheets().getByIndex(0)
    sheet.getCellRangeByName("A1:A3").setDataArray(
        (("apples",), ("bananas",), ("cherries",)))
    print("Seeded A1:A3 with fruit.\n")

    # 2. Launch the MCP server as a subprocess and speak JSON-RPC to it.
    env = dict(os.environ, LO_UNO_PORT=str(PORT))
    server = subprocess.Popen(
        [sys.executable, SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env)

    def call(mid, method, params=None):
        server.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": mid, "method": method,
             "params": params or {}}) + "\n")
        server.stdin.flush()
        return json.loads(server.stdout.readline())

    def notify(method):
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        server.stdin.flush()

    def tool(mid, name, args):
        resp = call(mid, "tools/call", {"name": name, "arguments": args})
        # content[0] is the human summary line; the structured JSON is content[-1].
        text = resp["result"]["content"][-1]["text"]
        return json.loads(text)

    init = call(1, "initialize", {"protocolVersion": "2024-11-05",
                                  "capabilities": {}, "clientInfo": {"name": "demo"}})
    notify("notifications/initialized")
    print("initialize -> server:", init["result"]["serverInfo"], "\n")

    print("tools/call lo_status        ->", tool(2, "lo_status", {}))
    print("tools/call calc_read_range  ->", tool(3, "calc_read_range", {"range": "A1:A3"}))
    print("tools/call calc_write_range ->",
          tool(4, "calc_write_range",
               {"range": "B1:B3",
                "cells": [["APPLES"], ["BANANAS"], ["CHERRIES"]]}))

    # 3. Verify (independently) that the MCP write actually changed the sheet.
    got = [r[0] for r in ub.read_range_grid(sheet.getCellRangeByName("B1:B3"))]
    print("\nB1:B3 in the real sheet after the MCP call:", got)
    assert got == ["APPLES", "BANANAS", "CHERRIES"], got

    server.stdin.close()
    server.wait(timeout=5)
    doc.close(False)
    print("\nDEMO OK: I drove LibreOffice via the MCP protocol — read A1:A3 and "
          "wrote B1:B3 through tools/call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
