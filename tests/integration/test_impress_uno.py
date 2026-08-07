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

        # --- Task 7: export the whole deck to PDF ---------------------------
        out = os.path.join(os.environ.get("TEMP", os.getcwd()), "impress_mvp_test.pdf")
        if os.path.exists(out):
            os.remove(out)
        exp = server.tool_export_document({"path": out, "format": "pdf"})
        _assert(os.path.exists(out) and os.path.getsize(out) > 0,
                "PDF not written: %r" % exp)
        os.remove(out)
        print("PASS: export_document -> impress_pdf_Export (real PDF)")

        # --- Increment 2, wave 1: transition + table + per-slide export -----
        server.tool_impress_set_transition({"all": True, "type": "fade",
                                             "duration": 1.5})
        pg1 = server._impress_pages().getByIndex(0)
        _assert(pg1.TransitionType != 0, "transition applied: %r" % pg1.TransitionType)
        print("PASS: impress_set_transition")

        ts = server.tool_impress_add_slide({"layout": "title_only"})["slide"]
        server.tool_impress_insert_table({"slide": ts, "data": [
            ["Region", "Q1", "Q2"], ["APAC", "120", "135"], ["EMEA", "90", "110"]]})
        model = None
        pg = server._impress_pages().getByIndex(ts - 1)
        for i in range(pg.getCount()):
            shp = pg.getByIndex(i)
            if hasattr(shp, "Model") and hasattr(shp.Model, "RowCount"):
                model = shp.Model
                break
        _assert(model is not None, "table shape present")
        _assert(model.RowCount == 3 and model.ColumnCount == 3,
                "table size: %sx%s" % (model.RowCount, model.ColumnCount))
        _assert(model.getCellByPosition(0, 0).getString() == "Region",
                "table cell content")
        print("PASS: impress_insert_table")

        import tempfile
        d = os.path.join(tempfile.gettempdir(), "impress_slides_probe")
        r = server.tool_impress_export_slides({"dir": d, "format": "png"})
        _assert(r["count"] >= 1 and all(os.path.getsize(f) > 0 for f in r["files"]),
                "png export: %r" % r)
        for f in r["files"]:
            os.remove(f)
        print("PASS: impress_export_slides (PNG per slide)")

        # --- Increment 2, wave 2: chart -------------------------------------
        cs = server.tool_impress_add_slide({"layout": "title_only"})["slide"]
        server.tool_impress_insert_chart({"slide": cs, "chart_type": "column",
            "title": "Sales", "data": [["", "2023", "2024"],
                                        ["APAC", 10, 14], ["EMEA", 8, 9]]})
        cpg = server._impress_pages().getByIndex(cs - 1)
        chart_model = None
        for i in range(cpg.getCount()):
            shp = cpg.getByIndex(i)
            if hasattr(shp, "CLSID") and getattr(shp, "CLSID", ""):
                chart_model = shp.Model
                break
        _assert(chart_model is not None, "chart OLE shape present")
        got = chart_model.getData().getData()
        _assert(len(got) == 2 and abs(got[0][0] - 10.0) < 1e-6,
                "chart data round-trip: %r" % (got,))
        print("PASS: impress_insert_chart")

        # --- Increment 2, wave 3: slideshow control (safe paths headless) ---
        st = server.tool_impress_slideshow({"action": "status"})
        _assert(st["running"] in (True, False), "status returns a bool: %r" % st)
        server.tool_impress_slideshow({"action": "stop"})   # safe no-op when idle
        print("PASS: impress_slideshow (status/stop; start needs a GUI session)")

        # --- Increment 2, wave 3: slide background (rendered technique) -----
        bs = server.tool_impress_add_slide({"layout": "blank"})["slide"]
        server.tool_impress_set_background({"slide": bs, "color": "#2E4053"})
        bpg = server._impress_pages().getByIndex(bs - 1)
        bg = None
        for i in range(bpg.getCount()):
            shp = bpg.getByIndex(i)
            if getattr(shp, "Name", "") == server._BG_SHAPE_NAME:
                bg = shp
        _assert(bg is not None, "background shape present")
        _assert(bg.FillColor == 0x2E4053, "background color: %r" % hex(bg.FillColor))
        # idempotent: setting again must not stack a second background rectangle
        server.tool_impress_set_background({"slide": bs, "color": "#802020"})
        n_bg = sum(1 for i in range(bpg.getCount())
                   if getattr(bpg.getByIndex(i), "Name", "") == server._BG_SHAPE_NAME)
        _assert(n_bg == 1, "background is idempotent, got %d" % n_bg)
        # image background at 60% transparency (bitmap fill, still one bg shape)
        bgpng = os.path.abspath(os.path.join(_HERE, "..", "..", "ext", "icons", "icon.png"))
        r = server.tool_impress_set_background({"slide": bs, "image": bgpng,
                                                "transparency": 60})
        _assert(r["image"] == "icon.png" and r["transparency"] == 60,
                "image bg echo: %r" % r)
        bg = next(bpg.getByIndex(i) for i in range(bpg.getCount())
                  if getattr(bpg.getByIndex(i), "Name", "") == server._BG_SHAPE_NAME)
        _assert(bg.FillTransparence == 60, "fill transparency: %r" % bg.FillTransparence)
        print("PASS: impress_set_background (color, image, transparency, idempotent)")

        print("\nALL IMPRESS TOOL CHECKS PASSED")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
