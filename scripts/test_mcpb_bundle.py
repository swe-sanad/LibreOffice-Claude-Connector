# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Simulate Claude Desktop's launch of the built .mcpb bundle end to end:

    python scripts/test_mcpb_bundle.py [--live]

Extracts dist/libreoffice-connector-<version>.mcpb to a temp dir (like the
Desktop app does), runs `node index.js` FROM THE EXTRACTED DIR with the same
env shape the manifest produces (LIBREOFFICE_PYTHON empty -> auto-detect),
then drives the MCP handshake over stdio: initialize, notifications/initialized,
tools/list. With --live it also calls lo_status (which may auto-launch
LibreOffice). Exits non-zero on any failure — run it before every release.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    live = "--live" in sys.argv
    manifest = json.load(io.open(os.path.join(ROOT, "mcpb", "manifest.json"),
                                 encoding="utf-8"))
    version = manifest["version"]
    bundle = os.path.join(ROOT, "dist", "libreoffice-connector-%s.mcpb" % version)
    if not os.path.exists(bundle):
        print("FAIL: bundle not built: %s (run scripts/build_mcpb.py)" % bundle)
        return 1

    with tempfile.TemporaryDirectory(prefix="mcpb-test-") as tmp:
        with zipfile.ZipFile(bundle) as z:
            z.extractall(tmp)
        entry = os.path.join(tmp, manifest["server"]["entry_point"])
        if not os.path.exists(entry):
            print("FAIL: entry_point %r missing from bundle" % manifest["server"]["entry_point"])
            return 1

        env = dict(os.environ)
        env["LIBREOFFICE_PYTHON"] = ""     # what an untouched config dialog produces
        env["LO_UNO_PORT"] = "2002"

        msgs = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "bundle-test", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ]
        if live:
            msgs.append({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "lo_status", "arguments": {}}})
        payload = "".join(json.dumps(m) + "\n" for m in msgs)

        proc = subprocess.run(["node", entry], input=payload.encode("utf-8"),
                              capture_output=True, timeout=120 if live else 30,
                              cwd=tmp, env=env)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")

        replies = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    m = json.loads(line)
                    if "id" in m:
                        replies[m["id"]] = m
                except ValueError:
                    pass

        ok = True

        def check(cond, label, detail=""):
            nonlocal ok
            print(("PASS " if cond else "FAIL ") + label + (("  " + detail) if detail else ""))
            ok = ok and cond

        init = replies.get(0, {}).get("result", {})
        check(init.get("serverInfo", {}).get("version") == version,
              "initialize", "server version %s" % init.get("serverInfo", {}).get("version"))
        tools = replies.get(1, {}).get("result", {}).get("tools", [])
        names = {t["name"] for t in tools}
        # default tier: a small everyday set, with dispatch as the way to the rest
        check(20 <= len(tools) <= 60, "tools/list (default tier)",
              "%d tools" % len(tools))
        check("dispatch" in names, "dispatch advertised",
              "the escape hatch to the unadvertised tools")
        for name in ("lo_status", "calc_overview", "calc_format_table",
                     "writer_format_document"):
            check(name in names, "everyday tool %s advertised" % name)
        check(any(f.endswith(".oxt") for f in zipfile.ZipFile(bundle).namelist()),
              "agent-acceptor .oxt bundled")
        if live:
            content = replies.get(2, {}).get("result", {})
            # content[0] is the human summary line; the JSON payload is content[-1].
            text = (content.get("content") or [{}])[-1].get("text", "")
            check("connected" in text and not content.get("isError"),
                  "lo_status (live)", text[:80])
        check("[libreoffice-connector] launching:" in err,
              "launcher diagnostics on stderr")
        check(proc.returncode == 0, "clean exit", "code %s" % proc.returncode)

        # The "Advertise all tools" checkbox reaches the server as LO_TOOLS="true"
        # (manifest booleans stringify). Prove that plumbing, not just LO_TOOLS=full.
        full_env = dict(env)
        full_env["LO_TOOLS"] = "true"
        full = subprocess.run(
            ["node", entry],
            input="".join(json.dumps(m) + "\n" for m in msgs[:3]).encode("utf-8"),
            capture_output=True, timeout=30, cwd=tmp, env=full_env)
        n_full = 0
        for line in full.stdout.decode("utf-8", "replace").splitlines():
            if line.strip().startswith("{"):
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == 1:
                    n_full = len(msg.get("result", {}).get("tools", []))
        check(n_full > len(tools), "tools/list (all-tools checkbox)",
              "%d tools vs %d default" % (n_full, len(tools)))

        if not ok:
            print("--- stderr ---")
            print(err[-2000:])
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
