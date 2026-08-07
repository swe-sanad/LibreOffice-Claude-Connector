# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Phase-0 probe for Impress increment 2: transitions, animations, tables, charts,
# master/background, per-slide PNG/SVG export, slideshow. Throwaway.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress2.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402


def _has(obj, *names):
    return {n: hasattr(obj, n) for n in names}


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    ctx = server._state["ctx"]
    smgr = server._state["smgr"]
    try:
        pages = doc.getDrawPages()
        page = pages.getByIndex(0)
        page.Layout = 1

        print("=== 1. TRANSITIONS: DrawPage properties ===")
        print("  ", _has(page, "Change", "Effect", "Speed", "TransitionType",
                          "TransitionSubtype", "TransitionDuration", "Duration"))
        try:
            page.TransitionType = 3      # e.g. FADE family
            page.TransitionSubtype = 1
            page.Change = 1              # 1 = automatic after Duration
            page.Duration = 2
            print("   set TransitionType/Subtype/Change/Duration OK ->",
                  page.TransitionType, page.TransitionSubtype, page.Change, page.Duration)
        except Exception as exc:
            print("   transition set FAILED:", exc)

        print("\n=== 2. ANIMATIONS: node supplier ===")
        print("  ", _has(page, "AnimationNode"))
        try:
            node = page.AnimationNode
            print("   page.AnimationNode:", node is not None, type(node).__name__ if node else None)
        except Exception as exc:
            print("   AnimationNode FAILED:", exc)
        # is there a high-level per-shape effect API? (MainSequence / XAnimationNode)
        try:
            from com.sun.star.presentation import EffectPresetClass  # noqa
            print("   EffectPresetClass importable: yes")
        except Exception as exc:
            print("   EffectPresetClass import:", exc)

        print("\n=== 3. TABLES ===")
        for svc in ("com.sun.star.presentation.TableShape",
                    "com.sun.star.drawing.TableShape"):
            try:
                t = doc.createInstance(svc)
                print("   created", svc, "->", t is not None,
                      "hasModel=", hasattr(t, "Model"))
            except Exception as exc:
                print("   createInstance", svc, "FAILED:", exc)

        print("\n=== 4. CHARTS (OLE2 with chart CLSID) ===")
        try:
            ole = doc.createInstance("com.sun.star.presentation.OLE2Shape")
            print("   OLE2Shape created:", ole is not None,
                  _has(ole, "CLSID", "Model", "PersistName"))
        except Exception as exc:
            print("   OLE2Shape FAILED:", exc)

        print("\n=== 5. MASTER / BACKGROUND ===")
        print("   doc.getMasterPages present:", hasattr(doc, "getMasterPages"))
        try:
            mp = doc.getMasterPages()
            print("   master count:", mp.getCount())
        except Exception as exc:
            print("   getMasterPages FAILED:", exc)
        print("   page has Background prop:", _has(page, "Background"))
        try:
            from com.sun.star.beans import PropertyValue  # noqa
            bg = doc.createInstance("com.sun.star.drawing.FillProperties")
            print("   FillProperties instance:", bg is not None)
        except Exception as exc:
            print("   FillProperties:", exc)

        print("\n=== 6. PER-SLIDE PNG/SVG EXPORT (GraphicExportFilter) ===")
        try:
            gef = smgr.createInstanceWithContext(
                "com.sun.star.drawing.GraphicExportFilter", ctx)
            gef.setSourceDocument(page)
            out = os.path.join(os.environ.get("TEMP", "."), "slide1_probe.png")
            from com.sun.star.beans import PropertyValue
            p1 = PropertyValue(); p1.Name = "URL"; p1.Value = server._to_url(out)
            p2 = PropertyValue(); p2.Name = "MediaType"; p2.Value = "image/png"
            gef.filter((p1, p2))
            print("   PNG export ->", os.path.exists(out),
                  os.path.getsize(out) if os.path.exists(out) else 0, "bytes")
            if os.path.exists(out):
                os.remove(out)
        except Exception as exc:
            print("   GraphicExportFilter FAILED:", exc)

        print("\n=== 7. SLIDESHOW ===")
        try:
            pres = doc.Presentation
            print("   doc.Presentation:", pres is not None,
                  _has(pres, "start", "end", "isRunning"))
        except Exception as exc:
            print("   Presentation FAILED:", exc)
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
