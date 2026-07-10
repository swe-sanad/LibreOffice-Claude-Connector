# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""LIVE test: the EXTENDED MCP tool set drives a real LibreOffice over UNO.

Run via the shared harness (starts an isolated headless office):

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_mcp_tools_extended.py

Covers document lifecycle, Calc formulas/structure/sheets/formatting/charts,
and the Writer content tools. No API key needed.
"""

import base64
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "mcp"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

import libreoffice_mcp as server

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))

# a 1x1 PNG for the image tool
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def check_calc(tmpdir):
    created = server.tool_create_document({"type": "calc"})
    _assert(created["created"]["type"] == "calc", created)
    print("PASS: create_document(calc)")

    # sheets
    server.tool_calc_add_sheet({"name": "Data"})
    listed = server.tool_calc_list_sheets({})
    _assert("Data" in listed["sheets"], listed)
    server.tool_calc_rename_sheet({"name": "Data", "new_name": "Numbers"})
    listed = server.tool_calc_list_sheets({})
    _assert("Numbers" in listed["sheets"] and "Data" not in listed["sheets"], listed)
    server.tool_calc_delete_sheet({"name": "Numbers"})
    _assert("Numbers" not in server.tool_calc_list_sheets({})["sheets"], "delete")
    print("PASS: calc_add_sheet / list / rename / delete")

    # data + formulas
    server.tool_calc_write_range({"range": "A1:B3",
                                  "cells": [["item", "qty"],
                                            ["nails", 10], ["screws", 5]]})
    server.tool_calc_set_formulas({"range": "C2:C3",
                                   "formulas": [["=B2*2"], ["=B3*2"]]})
    vals = server.tool_calc_read_range({"range": "C2:C3"})
    _assert(vals["cells"] == [[20.0], [10.0]], vals)
    formulas = server.tool_calc_get_formulas({"range": "C2:C3"})
    _assert(formulas["formulas"] == [["=B2*2"], ["=B3*2"]], formulas)
    print("PASS: calc_set_formulas / get_formulas (values computed)")

    used = server.tool_calc_get_used_range({})
    _assert(used["range"] == "A1:C3" and used["rows"] == 3, used)
    print("PASS: calc_get_used_range ->", used["range"])

    # structure
    server.tool_calc_insert_rows({"index": 1, "count": 1})
    shifted = server.tool_calc_read_range({"range": "A3:A3"})
    _assert(shifted["cells"] == [["nails"]], shifted)
    server.tool_calc_delete_rows({"index": 1, "count": 1})
    server.tool_calc_insert_columns({"index": 0, "count": 1})
    shifted = server.tool_calc_read_range({"range": "B1:B1"})
    _assert(shifted["cells"] == [["item"]], shifted)
    server.tool_calc_delete_columns({"index": 0, "count": 1})
    print("PASS: calc insert/delete rows & columns (shifts verified)")

    server.tool_calc_copy_range({"source_range": "A1:B3", "target_cell": "E1"})
    copied = server.tool_calc_read_range({"range": "E1:F1"})
    _assert(copied["cells"] == [["item", "qty"]], copied)
    print("PASS: calc_copy_range")

    hits = server.tool_calc_find_replace({"search": "nails", "replace": "NAILS"})
    _assert(hits["replacements"] == 2, hits)  # original + the copy
    print("PASS: calc_find_replace (%d replacements)" % hits["replacements"])

    server.tool_calc_clear_range({"range": "E1:F3"})
    cleared = server.tool_calc_read_range({"range": "E1:F3"})
    _assert(all(v == "" for row in cleared["cells"] for v in row), cleared)
    print("PASS: calc_clear_range")

    # formatting — verify through UNO that properties actually changed
    server.tool_calc_format_range({"range": "A1:B1", "bold": True,
                                   "background_color": "#FFEE00",
                                   "number_format": "0.00",
                                   "horizontal_align": "center",
                                   "auto_fit_columns": True})
    doc = server._current_doc()
    cell = doc.getSheets().getByIndex(0).getCellByPosition(0, 0)  # A1
    _assert(cell.CharWeight == 150.0, "bold not applied: %r" % cell.CharWeight)
    _assert(cell.CellBackColor == 0xFFEE00, "bg not applied: %r" % cell.CellBackColor)
    print("PASS: calc_format_range (bold + background verified via UNO)")

    server.tool_calc_merge_cells({"range": "A5:B5"})
    rng = doc.getSheets().getByIndex(0).getCellRangeByName("A5:B5")
    _assert(rng.getIsMerged(), "merge failed")
    server.tool_calc_merge_cells({"range": "A5:B5", "merge": False})
    _assert(not rng.getIsMerged(), "unmerge failed")
    print("PASS: calc_merge_cells (merge + unmerge)")

    chart = server.tool_calc_create_chart({"name": "QtyChart",
                                           "data_range": "A1:B3",
                                           "chart_type": "pie",
                                           "position_cell": "E5"})
    _assert(doc.getSheets().getByIndex(0).getCharts().hasByName("QtyChart"), chart)
    print("PASS: calc_create_chart (pie)")

    sel = server.tool_calc_select_range({"range": "B2:B3"})
    got = server.tool_get_current_selection({})
    _assert(got["range"]["startColumn"] == 1 and got["range"]["endRow"] == 2, got)
    print("PASS: calc_select_range + get_current_selection agree")

    # conditional formatting: highlight qty > 7 (nails=10 qualifies, screws=5 no)
    cf = server.tool_calc_add_conditional_format(
        {"range": "B2:B3", "operator": ">", "value": 7,
         "background_color": "#FF0000", "bold": True})
    _assert(cf["conditions"] >= 1, cf)
    b2 = doc.getSheets().getByIndex(0).getCellRangeByName("B2:B3")
    _assert(b2.getPropertyValue("ConditionalFormat").getCount() == 1,
            "conditional format not attached")
    _assert(doc.getStyleFamilies().getByName("CellStyles").hasByName(cf["style"]),
            "conditional cell style not created")
    print("PASS: calc_add_conditional_format (attached + style created)")
    cleared = server.tool_calc_clear_conditional_formats({"range": "B2:B3"})
    _assert(b2.getPropertyValue("ConditionalFormat").getCount() == 0, cleared)
    print("PASS: calc_clear_conditional_formats")

    # comments / annotations
    server.tool_calc_add_comment({"cell": "A1", "text": "header row"})
    server.tool_calc_add_comment({"cell": "A1", "text": "revised note"})  # upsert
    anns = doc.getSheets().getByIndex(0).getAnnotations()
    _assert(anns.getCount() == 1, "expected 1 annotation (upsert), got %d" % anns.getCount())
    got_comments = server.tool_calc_get_comments({})
    _assert(got_comments["comments"] == [{"sheet": doc.getSheets().getByIndex(0).getName(),
                                          "cell": "A1", "author": got_comments["comments"][0]["author"],
                                          "text": "revised note"}], got_comments)
    print("PASS: calc_add_comment (upsert) + calc_get_comments")

    # lifecycle: save as xlsx, export pdf, close
    xlsx = os.path.join(tmpdir, "mcp_test.xlsx")
    pdf = os.path.join(tmpdir, "mcp_test.pdf")
    saved = server.tool_save_document({"path": xlsx})
    _assert(os.path.getsize(xlsx) > 0, saved)
    exported = server.tool_save_document({"path": pdf, "format": "pdf"})
    _assert(os.path.getsize(pdf) > 0, exported)
    server.tool_close_document({})
    print("PASS: save_document (xlsx + pdf export) + close_document")

    # reopen the saved file
    opened = server.tool_open_document({"path": xlsx})
    _assert(opened["opened"]["type"] == "calc", opened)
    back = server.tool_calc_read_range({"range": "A2:A2"})
    _assert(back["cells"] == [["NAILS"]], back)
    server.tool_close_document({})
    print("PASS: open_document round-trip (data survived save/reload)")


def check_writer(tmpdir):
    created = server.tool_create_document({"type": "writer"})
    _assert(created["created"]["type"] == "writer", created)
    print("PASS: create_document(writer)")

    server.tool_writer_insert_heading({"text": "Report", "level": 1})
    server.tool_writer_append_text({"text": "Alpha beta gamma."})
    server.tool_writer_insert_heading({"text": "Details", "level": 2})
    server.tool_writer_append_text({"text": "More text\nSecond paragraph."})

    outline = server.tool_writer_get_outline({})
    _assert(outline["outline"] == [{"level": 1, "text": "Report"},
                                   {"level": 2, "text": "Details"}], outline)
    print("PASS: writer_insert_heading + writer_get_outline")

    hits = server.tool_writer_find_replace({"search": "beta", "replace": "BETA"})
    _assert(hits["replacements"] == 1, hits)
    text = server.tool_writer_get_text({})
    _assert("BETA" in text["text"], text)
    print("PASS: writer_find_replace")

    fmt = server.tool_writer_format_text({"search": "BETA", "bold": True,
                                          "font_color": "#CC0000"})
    _assert(fmt["matches_formatted"] == 1, fmt)
    doc = server._current_doc()
    desc = doc.createSearchDescriptor()
    desc.SearchString = "BETA"
    found = doc.findFirst(desc)
    _assert(found is not None and found.CharWeight == 150.0,
            "bold not applied to match")
    print("PASS: writer_format_text (bold verified via UNO)")

    table = server.tool_writer_insert_table({"rows": 2, "columns": 3,
                                             "data": [["a", "b", "c"],
                                                      [1, 2, 3]]})
    _assert(table["cells_filled"] == 6, table)
    _assert(doc.getTextTables().getCount() == 1, "table not in document")
    print("PASS: writer_insert_table")

    png = os.path.join(tmpdir, "dot.png")
    with open(png, "wb") as fh:
        fh.write(_PNG)
    img = server.tool_writer_insert_image({"path": png, "width_mm": 20,
                                           "height_mm": 20})
    _assert(doc.getGraphicObjects().getCount() == 1, img)
    print("PASS: writer_insert_image")

    server.tool_writer_insert_page_break({})
    server.tool_writer_append_text({"text": "Page two."})
    print("PASS: writer_insert_page_break")

    # comments anchored to a search string
    server.tool_writer_add_comment({"text": "check this figure",
                                    "search": "gamma", "author": "QA"})
    fields = doc.getTextFields().createEnumeration()
    ann_count = 0
    while fields.hasMoreElements():
        f = fields.nextElement()
        if f.supportsService("com.sun.star.text.TextField.Annotation"):
            ann_count += 1
    _assert(ann_count == 1, "expected 1 Writer annotation, got %d" % ann_count)
    got_c = server.tool_writer_get_comments({})
    _assert(len(got_c["comments"]) == 1 and got_c["comments"][0]["text"] == "check this figure"
            and got_c["comments"][0]["author"] == "QA", got_c)
    print("PASS: writer_add_comment (anchored) + writer_get_comments")

    # conditional section: the Condition is stored on the section; Writer's
    # layout evaluates it when the doc is viewed/printed (not observable headless).
    sec = server.tool_writer_add_conditional_section(
        {"name": "DraftOnly", "condition": "1==1", "text": "conditional note"})
    applied = doc.getTextSections().getByName("DraftOnly")
    _assert(applied.Condition == "1==1", "condition not stored: %r" % applied.Condition)
    print("PASS: writer_add_conditional_section (condition stored)")
    # explicit visible=false hides immediately (observable headless)
    sec2 = server.tool_writer_add_conditional_section(
        {"name": "HiddenNote", "condition": "", "text": "hidden note",
         "visible": False})
    _assert(sec2["currently_visible"] is False, "explicit hide should hide: %r" % sec2)
    print("PASS: writer_add_conditional_section (visible=false hides)")

    docx = os.path.join(tmpdir, "mcp_test.docx")
    pdf = os.path.join(tmpdir, "mcp_writer.pdf")
    server.tool_save_document({"path": docx})
    _assert(os.path.getsize(docx) > 0, "docx empty")
    server.tool_save_document({"path": pdf, "format": "pdf"})
    _assert(os.path.getsize(pdf) > 0, "pdf empty")
    server.tool_close_document({})
    print("PASS: writer save (docx + pdf export) + close")


def main():
    os.environ["LO_UNO_PORT"] = str(PORT)
    server._desktop()
    print("Connected on port", PORT)
    tmpdir = tempfile.mkdtemp(prefix="lo_mcp_ext_")
    check_calc(tmpdir)
    print()
    check_writer(tmpdir)
    print("\nALL EXTENDED MCP TOOL CHECKS PASSED (44-tool server drives real "
          "LibreOffice)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
