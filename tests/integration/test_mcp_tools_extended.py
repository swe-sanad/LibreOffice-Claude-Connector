# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
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

# com.sun.star.style.ParagraphAdjust.CENTER (pyuno returns the enum as an int)
CENTER_ADJUST = 3

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

    # table borders on a range
    bord = server.tool_calc_set_borders({"range": "A1:C3", "width_pt": 1.0,
                                         "color": "#0000FF"})
    tb = doc.getSheets().getByIndex(0).getCellRangeByName("A1:C3").getPropertyValue("TableBorder2")
    _assert(tb.TopLine.LineWidth > 0, "border not applied: %r" % bord)
    print("PASS: calc_set_borders")

    # form control: a push button on the sheet
    before = doc.getSheets().getByIndex(0).getDrawPage().getCount()
    server.tool_insert_form_control({"kind": "button", "label": "Run Claude",
                                     "name": "btnRun", "x_mm": 60, "y_mm": 5,
                                     "width_mm": 35, "height_mm": 10})
    dp = doc.getSheets().getByIndex(0).getDrawPage()
    _assert(dp.getCount() == before + 1, "control shape not added")
    _assert(dp.getByIndex(dp.getCount() - 1).getControl().Label == "Run Claude",
            "button label wrong")
    print("PASS: insert_form_control (button on Calc sheet)")

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

    # paragraph styling: center + 1.5x spacing on paragraphs containing "gamma"
    pf = server.tool_writer_format_paragraph(
        {"search": "gamma", "align": "center", "line_spacing_percent": 150,
         "space_above_mm": 3, "indent_left_mm": 10})
    _assert(pf["paragraphs_formatted"] >= 1, pf)
    d = doc.createSearchDescriptor()
    d.SearchString = "gamma"
    rng = doc.findFirst(d)
    _assert(rng.ParaAdjust == CENTER_ADJUST, "align not applied: %r" % rng.ParaAdjust)
    _assert(rng.ParaLineSpacing.Height == 150, "spacing not applied")
    _assert(rng.ParaLeftMargin == 1000, "indent not applied: %r" % rng.ParaLeftMargin)
    print("PASS: writer_format_paragraph (align + spacing + indent verified)")

    # page styling: A4 landscape with margins + 2 columns
    ps = server.tool_writer_set_page_style(
        {"paper": "a4", "orientation": "landscape",
         "margin_top_mm": 15, "margin_left_mm": 20, "columns": 2})
    style = doc.getStyleFamilies().getByName("PageStyles").getByName(ps["page_style"])
    _assert(style.IsLandscape and style.Size.Width > style.Size.Height,
            "landscape not applied: %r" % ps)
    # ±2 tolerance: LibreOffice round-trips mm through twips (15mm -> 1499)
    _assert(abs(style.TopMargin - 1500) <= 2 and abs(style.LeftMargin - 2000) <= 2,
            "margins not applied: top=%d left=%d" % (style.TopMargin, style.LeftMargin))
    _assert(style.TextColumns.getColumnCount() == 2, "columns not applied")
    print("PASS: writer_set_page_style (A4 landscape + margins + columns)")

    # header/footer
    server.tool_writer_set_header_footer(
        {"which": "header", "enable": True, "text": "Confidential"})
    _assert(style.HeaderIsOn and style.HeaderText.getString() == "Confidential",
            "header not set")
    print("PASS: writer_set_header_footer")

    # table formatting: border + styled header row on the earlier table
    ft = server.tool_writer_format_table(
        {"index": 0, "border_width_pt": 1.0, "border_color": "#333333",
         "header_bold": True, "header_background": "#DDDDDD"})
    tbl = doc.getTextTables().getByIndex(0)
    hdr = tbl.getCellByPosition(0, 0)
    _assert(hdr.BackColor == 0xDDDDDD, "header bg not applied: %r" % hdr.BackColor)
    print("PASS: writer_format_table (border + header row)")

    # form control: a checkbox in the Writer document
    before = doc.getDrawPage().getCount()
    server.tool_insert_form_control(
        {"kind": "checkbox", "label": "Approved", "name": "cbApproved",
         "x_mm": 20, "y_mm": 20, "width_mm": 40, "height_mm": 8})
    dp = doc.getDrawPage()
    _assert(dp.getCount() == before + 1, "control not added to Writer")
    _assert(dp.getByIndex(dp.getCount() - 1).getControl().Label == "Approved",
            "checkbox label wrong")
    print("PASS: insert_form_control (checkbox in Writer)")

    docx = os.path.join(tmpdir, "mcp_test.docx")
    pdf = os.path.join(tmpdir, "mcp_writer.pdf")
    server.tool_save_document({"path": docx})
    _assert(os.path.getsize(docx) > 0, "docx empty")
    server.tool_save_document({"path": pdf, "format": "pdf"})
    _assert(os.path.getsize(pdf) > 0, "pdf empty")
    server.tool_close_document({})
    print("PASS: writer save (docx + pdf export) + close")


def check_writer_paragraph_ops(_tmpdir):
    """writer_delete_paragraphs, writer_set_text_direction, and
    writer_format_paragraph's by-index targeting."""
    server.tool_create_document({"type": "writer"})
    doc = server._current_doc()
    text = doc.getText()

    # Build exactly six paragraphs P0..P5 with real paragraph breaks.
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    text.setString("")
    cur = text.createTextCursor()
    cur.gotoStart(False)
    for i, t in enumerate(["P0", "P1", "P2", "P3", "P4", "P5"]):
        text.insertString(cur, t, False)
        if i < 5:
            text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
    got = [p["text"] for p in server.tool_writer_get_paragraphs({})["paragraphs"]]
    _assert(got == ["P0", "P1", "P2", "P3", "P4", "P5"], "setup: %r" % got)
    print("PASS(setup): six body paragraphs")

    # Delete a MIDDLE range (P1, P2) — paras[end] survives and shifts up.
    res = server.tool_writer_delete_paragraphs({"start": 1, "count": 2})
    _assert(res["deleted"] == 2, res)
    got = [p["text"] for p in server.tool_writer_get_paragraphs({})["paragraphs"]]
    _assert(got == ["P0", "P3", "P4", "P5"], "middle delete: %r" % got)
    print("PASS: writer_delete_paragraphs (middle range)")

    # Delete THROUGH the last paragraph, with count over-clamped.
    res = server.tool_writer_delete_paragraphs({"start": 2, "count": 9})
    _assert(res["deleted"] == 2, res)
    got = [p["text"] for p in server.tool_writer_get_paragraphs({})["paragraphs"]]
    _assert(got == ["P0", "P3"], "tail delete: %r" % got)
    print("PASS: writer_delete_paragraphs (through last, count clamped)")

    # A 2x2 table so whole-doc RTL also flips table-cell paragraphs.
    server.tool_writer_insert_table({"rows": 2, "columns": 2,
                                     "data": [["a", "b"], ["c", "d"]]})
    d = server.tool_writer_set_text_direction({"direction": "rtl"})
    _assert(d["scope"] == "document" and d["paragraphs"] >= 2, d)
    _assert(d["table_cell_paragraphs"] >= 4, d)
    _assert(d["page_style_set"] is True, d)
    # Verify via UNO: first body paragraph RL_TB(1) + right-aligned(RIGHT=1).
    en = doc.getText().createEnumeration()
    first = en.nextElement()
    _assert(first.WritingMode == 1, "para WritingMode: %r" % first.WritingMode)
    _assert(first.ParaAdjust == 1, "para not right-aligned: %r" % first.ParaAdjust)
    _assert(server._page_style(doc).WritingMode == 1, "page style not RTL")
    tbl = doc.getTextTables().getByIndex(0)
    cell = tbl.getCellByName(tbl.getCellNames()[0])
    _assert(cell.createEnumeration().nextElement().WritingMode == 1,
            "table cell paragraph not RTL")
    print("PASS: writer_set_text_direction (rtl: paragraphs + cells + page)")

    # Targeted range flips ONLY body paragraph 0 back to ltr.
    r = server.tool_writer_set_text_direction(
        {"direction": "ltr", "start": 0, "count": 1})
    _assert(r["scope"] == "range" and r["paragraphs"] == 1, r)
    p0 = doc.getText().createEnumeration().nextElement()
    _assert(p0.WritingMode == 0 and p0.ParaAdjust == 0,
            "targeted ltr: %r/%r" % (p0.WritingMode, p0.ParaAdjust))
    print("PASS: writer_set_text_direction (targeted range ltr)")

    # writer_format_paragraph targeting by index (start/count).
    fp = server.tool_writer_format_paragraph(
        {"start": 0, "count": 1, "style_name": "Heading 1"})
    _assert(fp["paragraphs_formatted"] == 1, fp)
    p0 = doc.getText().createEnumeration().nextElement()
    _assert(p0.ParaStyleName == "Heading 1", "index restyle: %r" % p0.ParaStyleName)
    print("PASS: writer_format_paragraph (by index start/count)")

    server.tool_close_document({})


def check_menu_coverage_tools(_tmpdir):
    """writer_set_chapter_numbering, writer_apply_style, writer_change_case,
    writer_sort_table, writer_edit_table (cell text), form_control."""
    server.tool_create_document({"type": "writer"})
    doc = server._current_doc()

    # Tools menu: heading (chapter) numbering bound to ARABIC for 2 levels.
    server.tool_writer_insert_heading({"text": "Alpha", "level": 1})
    server.tool_writer_insert_heading({"text": "Beta", "level": 2})
    cn = server.tool_writer_set_chapter_numbering({"levels": 2, "numbering": "arabic"})
    _assert(cn["levels"] == 2, cn)
    from com.sun.star.style.NumberingType import ARABIC
    lvl0 = {p.Name: p.Value for p in doc.getChapterNumberingRules().getByIndex(0)}
    _assert(lvl0["NumberingType"] == ARABIC,
            "chapter numbering not set: %r" % lvl0.get("NumberingType"))
    print("PASS: writer_set_chapter_numbering (levels bound to ARABIC)")

    # Styles menu: create + apply a paragraph style and a character style.
    server.tool_writer_append_text({"text": "quote this line"})
    server.tool_writer_append_text({"text": "make bold word here"})
    server.tool_set_style({"family": "paragraph", "name": "ProposalQuote",
                           "italic": True, "font_size": 13})
    ap = server.tool_writer_apply_style(
        {"style": "ProposalQuote", "kind": "paragraph", "search": "quote this line"})
    _assert(ap["applied"] >= 1, ap)
    d = doc.createSearchDescriptor()
    d.SearchString = "quote this line"
    _assert(doc.findFirst(d).ParaStyleName == "ProposalQuote", "para style not applied")
    print("PASS: writer_apply_style (paragraph style via search)")
    server.tool_set_style({"family": "character", "name": "HotWord",
                           "bold": True, "font_color": "#CC0000"})
    ac = server.tool_writer_apply_style(
        {"style": "HotWord", "kind": "character", "search": "bold word"})
    _assert(ac["applied"] >= 1, ac)
    d2 = doc.createSearchDescriptor()
    d2.SearchString = "bold word"
    _assert(doc.findFirst(d2).CharStyleName == "HotWord", "char style not applied")
    print("PASS: writer_apply_style (character style via search)")

    # Format menu: upper-case a matched phrase.
    cc = server.tool_writer_change_case({"mode": "upper", "search": "quote this line"})
    _assert(cc["ranges_changed"] >= 1, cc)
    _assert("QUOTE THIS LINE" in server.tool_writer_get_text({})["text"],
            "upper-case not applied")
    print("PASS: writer_change_case (upper via search)")

    # Table menu: sort rows (numeric then string), and edit a cell after insert.
    server.tool_writer_insert_table(
        {"rows": 4, "columns": 2,
         "data": [["name", "qty"], ["Charlie", "3"], ["alice", "10"], ["Bob", "1"]]})
    st = server.tool_writer_sort_table({"index": 0, "key_column": 1})
    _assert(st["rows_sorted"] == 3, st)
    tbl = doc.getTextTables().getByIndex(0)
    col0 = [tbl.getCellByPosition(0, r).getString() for r in range(1, 4)]
    _assert(col0 == ["Bob", "Charlie", "alice"], "numeric sort: %r" % col0)
    print("PASS: writer_sort_table (numeric key, header pinned)")
    server.tool_writer_sort_table({"index": 0, "key_column": 0})
    col0 = [tbl.getCellByPosition(0, r).getString() for r in range(1, 4)]
    _assert(col0 == ["alice", "Bob", "Charlie"], "string sort: %r" % col0)
    print("PASS: writer_sort_table (string key, case-insensitive)")
    server.tool_writer_edit_table({"index": 0, "cell": "A1", "text": "Name"})
    _assert(tbl.getCellByName("A1").getString() == "Name", "cell text not set")
    print("PASS: writer_edit_table (set cell text after insert)")

    # Form menu: insert a checkbox, list it, then update its label + state.
    server.tool_insert_form_control(
        {"kind": "checkbox", "label": "Old", "name": "cbTest",
         "x_mm": 20, "y_mm": 20, "width_mm": 40, "height_mm": 8})
    names = [c["name"] for c in server.tool_form_control({"action": "list"})["controls"]]
    _assert("cbTest" in names, "control not listed: %r" % names)
    server.tool_form_control(
        {"action": "set", "name": "cbTest", "label": "Approved", "state": 1})
    forms = doc.getDrawPage().getForms()
    model = None
    for fi in range(forms.getCount()):
        f = forms.getByIndex(fi)
        for ci in range(f.getCount()):
            m = f.getByIndex(ci)
            if getattr(m, "Name", None) == "cbTest":
                model = m
    _assert(model is not None and model.Label == "Approved",
            "form label not updated: %r" % (model and model.Label))
    print("PASS: form_control (list + set label/state)")

    server.tool_close_document({})


def check_structural_tools(_tmpdir):
    """writer_move_paragraphs, writer_convert_table (both directions),
    writer_insert_caption, and set_style follow_style."""
    server.tool_create_document({"type": "writer"})
    doc = server._current_doc()
    text = doc.getText()
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK

    def build(items):
        text.setString("")
        cur = text.createTextCursor()
        cur.gotoStart(False)
        for i, s in enumerate(items):
            text.insertString(cur, s, False)
            if i < len(items) - 1:
                text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)

    def bodies():
        return [p["text"] for p in server.tool_writer_get_paragraphs({})["paragraphs"]]

    # move a paragraph down, then up
    build(["P0", "P1", "P2", "P3", "P4"])
    server.tool_writer_move_paragraphs({"start": 1, "count": 1, "to": 4})
    _assert(bodies() == ["P0", "P2", "P3", "P1", "P4"], "move down: %r" % bodies())
    print("PASS: writer_move_paragraphs (down)")
    build(["P0", "P1", "P2", "P3", "P4"])
    server.tool_writer_move_paragraphs({"start": 3, "count": 1, "to": 1})
    _assert(bodies() == ["P0", "P3", "P1", "P2", "P4"], "move up: %r" % bodies())
    print("PASS: writer_move_paragraphs (up)")

    # text -> table (rows 1..2 become a 2x2 table; keeps flank paragraphs)
    build(["keep0", "a,b", "c,d", "keep3"])
    r = server.tool_writer_convert_table(
        {"direction": "to_table", "start": 1, "count": 2, "separator": ","})
    _assert(r["rows"] == 2 and r["columns"] == 2, r)
    _assert(bodies() == ["keep0", "keep3"], "to_table paras: %r" % bodies())
    tbl = doc.getTextTables().getByIndex(0)
    _assert(tbl.getCellByName("A1").getString() == "a"
            and tbl.getCellByName("B2").getString() == "d", "to_table cells")
    print("PASS: writer_convert_table (text -> table)")

    # table -> text (round-trip the table back to rows)
    server.tool_writer_convert_table({"direction": "to_text", "index": 0})
    _assert(doc.getTextTables().getCount() == 0, "table not removed")
    b = bodies()
    _assert("a\tb" in b and "c\td" in b, "to_text paras: %r" % b)
    print("PASS: writer_convert_table (table -> text)")
    server.tool_close_document({})

    # caption: two auto-numbered captions in the same category
    server.tool_create_document({"type": "writer"})
    doc = server._current_doc()
    c1 = server.tool_writer_insert_caption({"category": "Figure", "text": "First"})
    c2 = server.tool_writer_insert_caption({"category": "Figure", "text": "Second"})
    _assert(c1["number"] == "1" and c2["number"] == "2",
            "caption numbers: %r / %r" % (c1.get("number"), c2.get("number")))
    txt = server.tool_writer_get_text({})["text"]
    _assert("Figure 1" in txt and "Figure 2" in txt, "caption text: %r" % txt[:120])
    print("PASS: writer_insert_caption (auto-numbering sequence)")

    # set_style follow_style (next paragraph style)
    server.tool_set_style({"family": "paragraph", "name": "IntroHead",
                           "bold": True, "follow_style": "Standard"})
    st = doc.getStyleFamilies().getByName("ParagraphStyles").getByName("IntroHead")
    _assert(st.FollowStyle == "Standard", "follow_style not set: %r" % st.FollowStyle)
    print("PASS: set_style (follow_style / next style)")

    server.tool_close_document({})


def check_niche_tools(_tmpdir):
    """writer_table_formula, writer_split_cells, writer_clear_formatting,
    writer_set_line_numbering."""
    server.tool_create_document({"type": "writer"})
    doc = server._current_doc()

    # in-cell table formula
    server.tool_writer_insert_table({"rows": 3, "columns": 1})
    tbl = doc.getTextTables().getByIndex(0)
    tbl.getCellByName("A1").setValue(10)
    tbl.getCellByName("A2").setValue(5)
    r = server.tool_writer_table_formula(
        {"index": 0, "cell": "A3", "formula": "=<A1>+<A2>"})
    _assert(r["value"] == 15.0, "formula value: %r" % r)
    _assert(tbl.getCellByName("A3").getValue() == 15.0, "cell not computed")
    print("PASS: writer_table_formula")

    # split a cell into 2 columns
    before = list(tbl.getCellNames())
    server.tool_writer_split_cells(
        {"index": 0, "cell": "A1", "into": 2, "direction": "columns"})
    after = list(doc.getTextTables().getByIndex(0).getCellNames())
    _assert(len(after) == len(before) + 1, "split: %r -> %r" % (before, after))
    print("PASS: writer_split_cells")

    # clear direct formatting
    server.tool_writer_append_text({"text": "formatted line"})
    server.tool_writer_format_text({"search": "formatted line", "bold": True})
    d = doc.createSearchDescriptor()
    d.SearchString = "formatted line"
    _assert(doc.findFirst(d).CharWeight == 150.0, "precondition: should be bold")
    server.tool_writer_clear_formatting({"search": "formatted line"})
    _assert(doc.findFirst(d).CharWeight == 100.0, "formatting not cleared")
    print("PASS: writer_clear_formatting")

    # line numbering
    ln = server.tool_writer_set_line_numbering({"enable": True, "interval": 5})
    _assert(ln["enabled"] is True and ln["interval"] == 5, ln)
    _assert(doc.getLineNumberingProperties().IsOn is True, "line numbering off")
    print("PASS: writer_set_line_numbering")

    server.tool_close_document({})


def check_doc_activation_tools(tmpdir):
    """set_active_document (switch focus between docs), writer_replace_image,
    writer_repeat_heading_rows."""
    # Open a Writer and a Calc doc; prove set_active_document switches focus.
    server.tool_create_document({"type": "writer"})
    server.tool_create_document({"type": "calc"})
    # Now the calc doc is focused; activate the writer by title and confirm a
    # writer-only op succeeds (would raise "not a Writer document" otherwise).
    docs = server.tool_list_documents({})["documents"]
    wtitle = next(d["title"] for d in docs if d["type"] == "writer")
    act = server.tool_set_active_document({"title": wtitle})
    _assert(act["active"]["type"] == "writer", "did not activate writer: %r" % act)
    server.tool_writer_append_text({"text": "now targeting the writer doc"})
    _assert("targeting the writer" in server.tool_writer_get_text({})["text"],
            "writer op did not hit the activated doc")
    print("PASS: set_active_document (focus switched to writer)")
    # Switch to the calc doc and confirm a calc op lands there.
    ctitle = next(d["title"] for d in docs if d["type"] == "calc")
    server.tool_set_active_document({"title": ctitle})
    server.tool_calc_write_range({"range": "A1:A1", "cells": [["hi"]]})
    _assert(server.tool_calc_read_range({"range": "A1:A1"})["cells"] == [["hi"]],
            "calc op did not hit the activated doc")
    print("PASS: set_active_document (focus switched to calc)")
    server.tool_close_document({})   # close calc
    server.tool_set_active_document({"title": wtitle})

    # writer_replace_image: insert an image, then resize it in place.
    png = os.path.join(tmpdir, "logo.png")
    with open(png, "wb") as fh:
        fh.write(_PNG)
    server.tool_writer_insert_image({"path": png, "width_mm": 10, "height_mm": 10})
    doc = server._current_doc()
    img = doc.getGraphicObjects().getByIndex(0)
    r = server.tool_writer_replace_image(
        {"name": img.Name, "width_mm": 40, "height_mm": 25})
    _assert("width" in r["changed"], r)
    _assert(abs(img.Width - 4000) <= 5 and abs(img.Height - 2500) <= 5,
            "image not resized: %d x %d" % (img.Width, img.Height))
    print("PASS: writer_replace_image (resize in place)")

    # writer_repeat_heading_rows
    server.tool_writer_insert_table({"rows": 4, "columns": 2})
    r = server.tool_writer_repeat_heading_rows({"index": 0, "rows": 1})
    _assert(r["repeat"] is True and r["header_rows"] == 1, r)
    _assert(doc.getTextTables().getByIndex(0).RepeatHeadline is True,
            "RepeatHeadline not set")
    print("PASS: writer_repeat_heading_rows")

    server.tool_close_document({})


def main():
    os.environ["LO_UNO_PORT"] = str(PORT)
    server._desktop()
    print("Connected on port", PORT)
    tmpdir = tempfile.mkdtemp(prefix="lo_mcp_ext_")
    check_calc(tmpdir)
    print()
    check_writer(tmpdir)
    print()
    check_writer_paragraph_ops(tmpdir)
    print()
    check_menu_coverage_tools(tmpdir)
    print()
    check_structural_tools(tmpdir)
    print()
    check_niche_tools(tmpdir)
    print()
    check_doc_activation_tools(tmpdir)
    print("\nALL EXTENDED MCP TOOL CHECKS PASSED (154-tool server drives real "
          "LibreOffice)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
