# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Verify the INSTALLED .oxt: does the component load, import its bundled
package, and register its ProtocolHandler?

Run against a LibreOffice instance whose profile already has the extension
installed (see scripts/install_and_verify.ps1). A non-null dispatch for our
command URLs proves the whole packaging + import chain works end to end:
if connector.py (or any `claudeconn` helper) failed to import, the component
could not instantiate and queryDispatch would return null.
"""

import os
import sys
import time

import uno

PORT = int(os.environ.get("LO_UNO_PORT", "2003"))
PROTOCOL = "com.swepioneers.claudeconnector"


def connect(port, retries=40, delay=0.5):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    url = "uno:socket,host=localhost,port=%d;urp;StarOffice.ComponentContext" % port
    last = None
    for _ in range(retries):
        try:
            ctx = resolver.resolve(url)
            smgr = ctx.ServiceManager
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            return ctx, smgr, desktop
        except Exception as exc:
            last = exc
            time.sleep(delay)
    raise RuntimeError("connect failed: %s" % last)


def _resolves(ctx, smgr, frame, command):
    transformer = smgr.createInstanceWithContext(
        "com.sun.star.util.URLTransformer", ctx)
    url = uno.createUnoStruct("com.sun.star.util.URL")
    url.Complete = PROTOCOL + ":" + command
    url = transformer.parseStrict(url)[1]   # parseStrict returns (bool, URL)
    dispatch = frame.queryDispatch(url, "_self", 0)
    return dispatch is not None


def main():
    ctx, smgr, desktop = connect(PORT)
    print("Connected on port", PORT)
    doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, ())
    try:
        frame = doc.getCurrentController().getFrame()
        # On a freshly-installed profile the extension's ProtocolHandler config
        # can take a few seconds after the socket opens to become active, so we
        # poll rather than checking once.
        ok = {}
        deadline = time.time() + 60
        while time.time() < deadline:
            ok = {cmd: _resolves(ctx, smgr, frame, cmd)
                  for cmd in ("Transform", "Settings")}
            if all(ok.values()):
                break
            time.sleep(2)
        for cmd, resolved in ok.items():
            print("dispatch %-10s -> %s" % (cmd, "RESOLVED" if resolved else "NULL"))
        if not all(ok.values()):
            raise AssertionError(
                "ProtocolHandler did not resolve — component import/registration "
                "failed. Check connector.py imports and ProtocolHandler.xcu.")
        print("\nPASS: extension installed, connector.py imported its "
              "'claudeconn' package, and the ProtocolHandler is registered.")

        # The sidebar panel factory component must also load + register (proves
        # sidebar_panel.py imports cleanly and is registered under IMPL_NAME).
        factory = smgr.createInstanceWithContext(
            "com.swepioneers.claudeconnector.SidebarFactory", ctx)
        print("sidebar factory   ->", "INSTANTIATED" if factory else "NULL")
        if factory is None:
            raise AssertionError(
                "sidebar_panel.py factory did not register — check its imports "
                "and Factories.xcu FactoryImplementation.")
        print("PASS: sidebar panel factory component registered.")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
