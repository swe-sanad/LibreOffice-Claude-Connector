# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Phase-0 spike (round 4): distinguish LAYOUT PLACEHOLDERS from INSERTED shapes.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402

PRES = "com.sun.star.presentation."


def _describe(shp, label):
    svcs = [s for s in shp.SupportedServiceNames
            if s.startswith(PRES) or s.endswith("GraphicObjectShape")
            or s.endswith("RectangleShape") or s.endswith("TextShape")]
    props = {}
    for p in ("IsEmptyPresentationObject", "IsPlaceholderDependent"):
        try:
            props[p] = getattr(shp, p)
        except Exception:
            props[p] = "<no prop>"
    print("  %-16s svc=%s props=%s" % (label, svcs, props))


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        pages = doc.getDrawPages()

        print("=== Title-subtitle slide: the two placeholders ===")
        p0 = pages.getByIndex(0)
        p0.Layout = 0
        for i in range(p0.getCount()):
            _describe(p0.getByIndex(i), "placeholder#%d" % i)

        print("\n=== Blank slide + inserted rectangle & text box ===")
        blank = pages.insertNewByIndex(0)
        blank.Layout = 20
        rect = doc.createInstance("com.sun.star.drawing.RectangleShape")
        blank.add(rect)
        rect.setString("box")
        tb = doc.createInstance("com.sun.star.drawing.TextShape")
        blank.add(tb)
        tb.setString("free text")
        for i in range(blank.getCount()):
            _describe(blank.getByIndex(i), "inserted#%d" % i)
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
