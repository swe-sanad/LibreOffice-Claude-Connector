# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LIVE test: the draw_* MCP tools drive a real LibreOffice Draw over UNO.

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_draw_uno.py -Port 2003

No API key needed. Exits non-zero on any failure.
"""

import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "mcp"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

import libreoffice_mcp as server  # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/sdraw", "_blank", 0, ())
    print("Connected; blank drawing open")
    try:
        ov = server.tool_draw_overview({})
        _assert(ov["count"] >= 1, "draw overview count: %r" % ov)
        print("PASS: draw_overview")

        r = server.tool_draw_add_page({"name": "Diagram"})
        _assert(r["count"] == ov["count"] + 1, "add_page count: %r" % r)
        print("PASS: draw_add_page")

        server.tool_draw_insert_shape({"page": 1, "kind": "rectangle",
            "x_mm": 20, "y_mm": 20, "width_mm": 40, "height_mm": 20,
            "text": "Start", "fill_color": "#4472C4"})
        server.tool_draw_insert_shape({"page": 1, "kind": "ellipse",
            "x_mm": 100, "y_mm": 20, "width_mm": 40, "height_mm": 20, "text": "End"})
        server.tool_draw_insert_text_box({"page": 1, "text": "Flow",
            "x_mm": 20, "y_mm": 60, "width_mm": 60, "height_mm": 12})
        png = os.path.abspath(os.path.join(_HERE, "..", "..", "ext", "icons", "icon.png"))
        server.tool_draw_insert_image({"page": 1, "path": png,
            "x_mm": 20, "y_mm": 80, "width_mm": 20, "height_mm": 20})
        # connector gluing the two shapes (indices 1 and 2 on the page)
        server.tool_draw_insert_connector({"page": 1, "x1_mm": 60, "y1_mm": 30,
            "x2_mm": 100, "y2_mm": 30, "start_shape": 1, "end_shape": 2})

        rp = server.tool_draw_read_page({"page": 1})
        kinds = [s["kind"] for s in rp["shapes"]]
        _assert(len(rp["shapes"]) >= 5, "expected >=5 shapes, got %r" % kinds)
        _assert(any("Connector" in k for k in kinds), "connector present: %r" % kinds)
        _assert(any(s["text"] == "Start" for s in rp["shapes"]), "shape text kept")
        print("PASS: draw_insert_shape/text_box/image/connector + draw_read_page")

        # export the drawing to PDF via the kind-aware export
        out = os.path.join(os.environ.get("TEMP", os.getcwd()), "draw_probe.pdf")
        if os.path.exists(out):
            os.remove(out)
        server.tool_export_document({"path": out, "format": "pdf"})
        _assert(os.path.exists(out) and os.path.getsize(out) > 0, "draw PDF export")
        os.remove(out)
        print("PASS: export_document -> draw_pdf_Export")

        print("\nALL DRAW TOOL CHECKS PASSED")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
