# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Phase-0 discovery spike (round 3) for the impress_* MVP. Throwaway.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        pages = doc.getDrawPages()
        page = pages.getByIndex(0)

        print("=== Layout -> getCount (find title_only) ===")
        for layout in (18, 19, 20):
            page.Layout = layout
            print("  Layout %d -> getCount=%d" % (layout, page.getCount()))

        print("\n=== Notes page: FULL services + write test ===")
        page.Layout = 1
        notes = page.getNotesPage()
        for i in range(notes.getCount()):
            shp = notes.getByIndex(i)
            full = list(shp.SupportedServiceNames)
            interesting = [s for s in full if s.rsplit(".", 1)[-1] in (
                "TitleTextShape", "NotesTextShape", "PageShape", "OutlinerShape",
                "SubtitleTextShape")]
            has_text = shp.supportsService("com.sun.star.drawing.Text")
            print("  notes#%d has_text=%s subtypes=%s" % (i, has_text, interesting))
        # write to the text-bearing notes shape
        target = None
        for i in range(notes.getCount()):
            shp = notes.getByIndex(i)
            if shp.supportsService("com.sun.star.presentation.NotesTextShape"):
                target = shp
                break
        if target is None:
            # fallback: last text-supporting shape that isn't the page thumbnail
            for i in range(notes.getCount()):
                shp = notes.getByIndex(i)
                if shp.supportsService("com.sun.star.drawing.Text") and \
                   not shp.supportsService("com.sun.star.presentation.PageShape"):
                    target = shp
        if target is not None:
            target.setString("SPEAKER NOTES HERE")
            print("  wrote notes -> readback=%r" % target.getString())
        else:
            print("  NO notes text shape identified")

        print("\n=== Subtitle placeholder (layout 0, index 1) full services ===")
        page.Layout = 0
        sub = page.getByIndex(1)
        print("  subtitle#1 subtypes=%s has_text=%s"
              % ([s for s in sub.SupportedServiceNames if "presentation" in s],
                 sub.supportsService("com.sun.star.drawing.Text")))

        print("\n=== GraphicProvider queryGraphic sanity (path->Graphic) ===")
        try:
            import mcp.libreoffice_mcp as s2
            ctx = s2._state["ctx"]
            gp = ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.graphic.GraphicProvider", ctx)
            print("  GraphicProvider created OK:", gp is not None,
                  "queryGraphic present:", hasattr(gp, "queryGraphic"))
        except Exception as exc:
            print("  GraphicProvider FAILED:", exc)
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
