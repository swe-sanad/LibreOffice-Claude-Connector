# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Visual confirmation of the SHIPPED impress tools: build a real slide with a
# background + title + bullets + a shape, render it, and eyeball the PNG.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress2.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402

OUT = os.path.join(os.environ.get("TEMP", "."), "impress_visual")


def main():
    os.makedirs(OUT, exist_ok=True)
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        # slide 1 already exists; make it a real title+content slide via the TOOLS
        server.tool_impress_set_layout({"slide": 1, "layout": "title_content"})
        server.tool_impress_set_background({"slide": 1, "color": "#1F3864"})
        server.tool_impress_set_title({"slide": 1, "text": "Quarterly Review"})
        server.tool_impress_set_content({"slide": 1, "bullets": [
            "Revenue up 12% year over year",
            {"text": "APAC led growth", "level": 1},
            "Risks: supply chain"]})
        server.tool_impress_insert_shape({"slide": 1, "kind": "ellipse",
            "x_mm": 200, "y_mm": 20, "width_mm": 60, "height_mm": 40,
            "text": "NEW", "fill_color": "#E67E22"})
        # render slide 1 with the shipped tool
        r = server.tool_impress_export_slides({"dir": OUT, "format": "png", "slide": 1})
        print("RENDERED:", r["files"][0])
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
