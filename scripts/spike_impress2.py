# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Probe + RENDER: image background and fill transparency for the background tool.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress2.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402

OUT = os.path.join(os.environ.get("TEMP", "."), "bg2")
IMG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ext", "icons", "icon.png"))


def _full_rect(doc, page):
    from com.sun.star.drawing.LineStyle import NONE as LNONE
    rect = doc.createInstance("com.sun.star.drawing.RectangleShape")
    page.add(rect)
    p = server._uno_struct("com.sun.star.awt.Point"); p.X = 0; p.Y = 0
    s = server._uno_struct("com.sun.star.awt.Size"); s.Width = page.Width; s.Height = page.Height
    rect.setPosition(p); rect.setSize(s)
    try:
        rect.LineStyle = LNONE
    except Exception:
        pass
    return rect


def _export(page, name):
    gef = server._state["smgr"].createInstanceWithContext(
        "com.sun.star.drawing.GraphicExportFilter", server._state["ctx"])
    gef.setSourceDocument(page)
    path = os.path.join(OUT, name)
    gef.filter((server._pv("URL", server._to_url(path)),
                server._pv("MediaType", "image/png")))
    print("wrote", name, os.path.getsize(path) if os.path.exists(path) else "MISSING")


def main():
    os.makedirs(OUT, exist_ok=True)
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        # Build a real slide with the SHIPPED tools: image background at 70%
        # transparency (faint watermark) + title + bullets.
        server.tool_impress_set_layout({"slide": 1, "layout": "title_content"})
        server.tool_impress_set_background({"slide": 1, "image": IMG,
                                            "transparency": 70})
        server.tool_impress_set_title({"slide": 1, "text": "Image Background"})
        server.tool_impress_set_content({"slide": 1, "bullets": [
            "Faint watermark image behind the text",
            {"text": "transparency = 70", "level": 1}]})
        r = server.tool_impress_export_slides({"dir": OUT, "format": "png", "slide": 1})
        print("RENDERED:", r["files"][0])
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
