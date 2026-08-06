# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LIVE test: the impress_* MCP tools drive a real LibreOffice Impress over UNO.

Run via the shared harness (starts an ISOLATED headless office and forces the
socket so it never touches the user's real session):

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_impress_uno.py -Port 2003

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
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    print("Connected; blank presentation open")
    try:
        # --- Task 2: add_slide + overview -----------------------------------
        before = server.tool_impress_overview({})["count"]
        r = server.tool_impress_add_slide({"layout": "title_content"})
        _assert(r["count"] == before + 1, "count after add: %r" % r)
        _assert(r["layout"] == "title_content", "layout echoed: %r" % r)
        ov = server.tool_impress_overview({})
        _assert(ov["count"] == before + 1, "overview count: %r" % ov)
        _assert(ov["slides"][-1]["layout"] == "title_content",
                "last slide layout: %r" % ov["slides"][-1])
        _assert(ov["slides"][-1]["index"] == before + 1, "1-based index: %r" % ov)
        print("PASS: impress_add_slide + impress_overview")

        # insert a blank slide AFTER slide 1 -> it becomes slide 2
        n = server.tool_impress_overview({})["count"]
        r2 = server.tool_impress_add_slide({"after": 1, "layout": "blank"})
        _assert(r2["slide"] == 2, "new slide number after=1: %r" % r2)
        ov = server.tool_impress_overview({})
        _assert(ov["count"] == n + 1, "insert-after count: %r" % ov)
        _assert(ov["slides"][1]["layout"] == "blank",
                "inserted slide landed at position 2: %r" % ov["slides"][1])
        print("PASS: impress_add_slide after a 1-based slide")

        # --- Task 3: set_title + set_content + read_slide -------------------
        s = server.tool_impress_add_slide({"layout": "title_content"})["slide"]
        server.tool_impress_set_title({"slide": s, "text": "Quarterly Review"})
        server.tool_impress_set_content({"slide": s, "bullets": [
            "Revenue up 12%",
            {"text": "APAC detail", "level": 1},
            {"text": "Risks", "level": 0},
        ]})
        rs = server.tool_impress_read_slide({"slide": s})
        _assert(rs["title"] == "Quarterly Review", "title readback: %r" % rs)
        _assert([b["text"] for b in rs["bullets"]] ==
                ["Revenue up 12%", "APAC detail", "Risks"],
                "bullets readback: %r" % rs["bullets"])
        _assert(rs["bullets"][1]["level"] == 1, "indent level: %r" % rs["bullets"])
        _assert(rs["bullets"][0]["level"] == 0, "top level: %r" % rs["bullets"])
        print("PASS: impress_set_title + impress_set_content + impress_read_slide")

        # --- Task 4: speaker notes -----------------------------------------
        server.tool_impress_set_notes({"slide": s, "text": "Pause for questions."})
        _assert(server.tool_impress_read_slide({"slide": s})["notes"] ==
                "Pause for questions.", "notes readback")
        _assert(server.tool_impress_overview({})["slides"][s - 1]["has_notes"],
                "overview reports has_notes")
        print("PASS: impress_set_notes")

        # --- Task 5: image + shape + text box ------------------------------
        blank = server.tool_impress_add_slide({"layout": "blank"})["slide"]
        png = os.path.abspath(os.path.join(_HERE, "..", "..", "ext", "icons", "icon.png"))
        _assert(os.path.exists(png), "test image missing: %s" % png)
        ri = server.tool_impress_insert_image(
            {"slide": blank, "path": png, "x_mm": 20, "y_mm": 20,
             "width_mm": 30, "height_mm": 30})
        _assert(ri["inserted"] == "icon.png", "image inserted: %r" % ri)
        server.tool_impress_insert_shape(
            {"slide": blank, "kind": "rectangle", "x_mm": 60, "y_mm": 20,
             "width_mm": 40, "height_mm": 20, "text": "Box", "fill_color": "#4472C4"})
        server.tool_impress_insert_text_box(
            {"slide": blank, "text": "Free text", "x_mm": 20, "y_mm": 60,
             "width_mm": 80, "height_mm": 15})
        # all three are real (non-placeholder) shapes on the blank slide
        shapes = server.tool_impress_read_slide({"slide": blank})["shapes"]
        _assert(len(shapes) >= 3, "expected >=3 inserted shapes, got %r" % shapes)
        print("PASS: impress_insert_image + impress_insert_shape + impress_insert_text_box")

        # --- Task 6: set_layout + duplicate + delete -----------------------
        target = server.tool_impress_add_slide({"layout": "title_only"})["slide"]
        before = server.tool_impress_overview({})["count"]
        dup = server.tool_impress_duplicate_slide({"slide": target})
        _assert(dup["count"] == before + 1, "duplicate count: %r" % dup)
        _assert(dup["slide"] == target + 1, "duplicate lands after: %r" % dup)
        server.tool_impress_set_layout({"slide": target, "layout": "two_content"})
        _assert(server.tool_impress_read_slide({"slide": target})["layout"] ==
                "two_content", "layout changed")
        # delete the duplicate we just made
        after_del = server.tool_impress_delete_slide({"slide": target + 1})
        _assert(after_del["count"] == before, "delete count: %r" % after_del)
        print("PASS: impress_set_layout + impress_duplicate_slide + impress_delete_slide")

        print("\nALL IMPRESS TOOL CHECKS PASSED")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
