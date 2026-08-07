# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Phase-0 probe round 3: reliable Impress slide-background recipe.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress2.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        from com.sun.star.drawing.FillStyle import SOLID
        page = doc.getDrawPages().getByIndex(0)
        master = doc.getMasterPages().getByIndex(0)

        print("=== master.Background ===")
        try:
            mbg = master.Background
            print("  master.Background:", mbg)
        except Exception as exc:
            print("  master.Background FAILED:", exc)

        # Recipe: instantiate the fill bean via the GLOBAL service manager,
        # not the document, then assign to page.Background and reassign.
        print("=== fill bean via global smgr ===")
        smgr = server._state["smgr"]
        for svc in ("com.sun.star.drawing.FillProperties",):
            try:
                obj = smgr.createInstance(svc)
                print("  smgr.createInstance", svc, "->", obj)
            except Exception as exc:
                print("  smgr.createInstance", svc, "FAILED:", exc)

        # Recipe: set on page via reassign using a bean cloned from a shape's
        # fill? Try assigning a RectangleShape's props is not valid. Instead try
        # the documented empty-string service on the DrawPage's own factory.
        print("=== page as its own factory ===")
        try:
            bean = page.Background            # None
            print("  before:", bean)
            # In LO the Background bean is created by the page's model:
            bean = doc.createInstance("com.sun.star.drawing.FillProperties")
            print("  doc.createInstance FillProperties ok")
        except Exception as exc:
            print("  doc factory FAILED:", exc)

        # Recipe that historically works: set FillStyle/FillColor on the page's
        # background via a freshly built PropertySet from the page itself.
        print("=== try page.setPropertyValue with master-derived bean ===")
        try:
            # get a background bean off the master if it exposes one
            mbg = master.Background
            if mbg is not None:
                mbg.FillStyle = SOLID
                mbg.FillColor = 0x1F3864
                master.Background = mbg
                print("  master bg set; readback:", hex(master.Background.FillColor))
            else:
                print("  master.Background is None too")
        except Exception as exc:
            print("  master bg recipe FAILED:", exc)
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
