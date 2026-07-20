# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Integration test: the agent-acceptor extension makes a flag-less LibreOffice
reachable over a named pipe.

Run via scripts/run_acceptor_test.ps1 — it installs the .oxt into an ISOLATED
profile, warm-boots to activate it, then starts soffice with NO --accept
argument. This test then must be able to:

  1. resolve  uno:pipe,name=<CLAUDE_AGENT_PIPE>;urp;StarOffice.ComponentContext
  2. prove the office answering the pipe is OUR isolated instance (never a
     user session) via PathSettings.UserConfig containing the profile marker
  3. do real work over the bridge (create a hidden Calc doc, write/read a cell)

Exit codes: 0 ok · 7 foreign instance · 9 pipe never became resolvable ·
1 work-over-bridge failed.
"""
import os
import sys
import time

import uno
from com.sun.star.beans import PropertyValue

PIPE = os.environ.get("CLAUDE_AGENT_PIPE", "")
MARKER = os.environ.get("LO_PROFILE_MARKER", "lo_acceptor_profile")
DEADLINE = time.time() + float(os.environ.get("PIPE_DEADLINE_SEC", "60"))


def log(msg):
    sys.stderr.write("[acceptor-test] %s\n" % msg)
    sys.stderr.flush()


def main():
    if not PIPE:
        log("CLAUDE_AGENT_PIPE not set")
        return 2
    url = "uno:pipe,name=%s;urp;StarOffice.ComponentContext" % PIPE
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)

    ctx = None
    last = None
    while time.time() < DEADLINE:
        try:
            ctx = resolver.resolve(url)
            break
        except Exception as exc:      # NoConnectException while the Job spins up
            last = exc
            time.sleep(1.0)
    if ctx is None:
        log("pipe %r never became resolvable: %s" % (PIPE, last))
        return 9
    log("pipe resolved")

    smgr = ctx.ServiceManager
    ps = smgr.createInstanceWithContext("com.sun.star.util.PathSettings", ctx)
    if MARKER not in ps.UserConfig:
        log("office on the pipe is NOT the isolated test instance (%s)" % ps.UserConfig)
        return 7
    log("own-instance check passed")

    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    hidden = PropertyValue()
    hidden.Name, hidden.Value = "Hidden", True
    doc = desktop.loadComponentFromURL(
        "private:factory/scalc", "_blank", 0, (hidden,))
    try:
        cell = doc.getSheets().getByIndex(0).getCellByPosition(0, 0)
        cell.setValue(42)
        if cell.getValue() != 42:
            log("cell round-trip failed")
            return 1
    finally:
        doc.close(False)
    log("work over the pipe bridge OK")
    print("ACCEPTOR-TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
