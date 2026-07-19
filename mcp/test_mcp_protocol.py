# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Protocol smoke test for the LibreOffice MCP server — no LibreOffice needed.

Spawns the server, performs the MCP handshake, lists tools, and pings. Run with
any Python 3.8+ (tools are lazy, so `uno` is not imported here):

    python mcp/test_mcp_protocol.py
"""

import json
import os
import subprocess
import sys

SERVER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "libreoffice_mcp.py")


def main():
    proc = subprocess.Popen(
        [sys.executable, SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # notification
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    out, _err = proc.communicate(stdin, timeout=30)

    responses = [json.loads(line) for line in out.splitlines() if line.strip()]
    by_id = {r.get("id"): r for r in responses}

    assert len(responses) == 3, "expected 3 responses (notification has none): %r" % responses
    assert by_id[1]["result"]["serverInfo"]["name"] == "libreoffice", responses
    assert by_id[1]["result"]["protocolVersion"] == "2024-11-05", responses
    tools = by_id[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    required = {"lo_status", "get_current_selection",
                "calc_read_range", "calc_write_range",
                "writer_get_text", "writer_replace_selection"}
    assert required <= names, "missing tools: %r" % (required - names)
    assert by_id[3]["result"] == {}, "ping should return {}: %r" % responses

    print("PASS: MCP handshake ok; tools/list has %d tools (%s); ping ok."
          % (len(tools), ", ".join(sorted(names))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
