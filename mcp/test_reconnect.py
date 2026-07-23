# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Reconnect test for the LibreOffice MCP server — no LibreOffice needed.

The server caches its UNO connection; if the office is restarted the cached
bridge is disposed. This checks that a disposed bridge triggers exactly one
reset+reconnect+retry (so the server survives an office restart) while a genuine
tool bug is surfaced, not retried. Tools are lazy, so `uno` is never imported.

    python mcp/test_reconnect.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import libreoffice_mcp as srv


def _call(name):
    return srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": {}}})


def main():
    # 1) classifier: a lost/disposed bridge vs a genuine tool error
    class DisposedException(Exception):
        pass
    assert srv._is_connection_error(RuntimeError("Binary URP bridge already disposed"))
    assert srv._is_connection_error(RuntimeError("Binary URP bridge disposed during call"))
    assert srv._is_connection_error(DisposedException("gone"))
    assert srv._is_connection_error(RuntimeError("NoConnectException: connection refused"))
    assert not srv._is_connection_error(ValueError("cells shape 2x2 does not match (3x3)"))
    assert not srv._is_connection_error(RuntimeError("No sheet named 'foo'"))

    # 2) reset drops the cached connection
    srv._state.update(ctx="x", smgr="y", desktop="z")
    srv._reset_connection()
    assert srv._state == {"ctx": None, "smgr": None, "desktop": None,
                          "transport": None}

    # 3) a disposed bridge is reset + retried ONCE, and the retry succeeds
    calls = {"n": 0}

    def flaky(_args):
        calls["n"] += 1
        if calls["n"] == 1:
            srv._state["desktop"] = "STALE"          # a cached, now-dead connection
            raise RuntimeError("Binary URP bridge already disposed")
        return {"attempt": calls["n"], "reset_before_retry": srv._state["desktop"] is None}

    srv.TOOLS["_probe_flaky"] = flaky
    try:
        resp = _call("_probe_flaky")
        # content[0] is the human summary; the JSON payload is content[-1].
        payload = json.loads(resp["result"]["content"][-1]["text"])
        assert calls["n"] == 2, "expected one retry, got %d calls" % calls["n"]
        assert payload["reset_before_retry"] is True, "must reset the dead conn before retrying"
        assert not resp["result"].get("isError"), "retry should succeed"

        # 4) a genuine tool bug is NOT retried and is surfaced as an error
        hits = {"n": 0}

        def buggy(_args):
            hits["n"] += 1
            raise ValueError("genuine tool bug")

        srv.TOOLS["_probe_buggy"] = buggy
        resp2 = _call("_probe_buggy")
        assert hits["n"] == 1, "a real bug must not be retried (got %d)" % hits["n"]
        assert resp2["result"].get("isError")
        assert "genuine tool bug" in resp2["result"]["content"][0]["text"]
    finally:
        srv.TOOLS.pop("_probe_flaky", None)
        srv.TOOLS.pop("_probe_buggy", None)

    print("PASS: disposed bridge -> reset+retry once; real tool bug not retried.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
