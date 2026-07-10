# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A Model Context Protocol (MCP) server for LibreOffice.

This is the INVERSE of the .oxt extension: instead of embedding Claude inside
LibreOffice, this lets an external MCP client (Claude Code / Claude Desktop /
Cowork) reach IN and drive LibreOffice as a tool — document lifecycle, Calc
data/formulas/formatting/structure/charts, Writer text/headings/tables/images,
find & replace in both.

Design goals (matching the rest of this repo):
  * **Standard library only** — implements MCP's JSON-RPC-2.0-over-stdio
    transport by hand (newline-delimited JSON). No `mcp` pip package needed.
  * **Runs under LibreOffice's bundled Python** so the `uno` module is available:
        "C:\\Program Files\\LibreOffice\\program\\python.exe" mcp/libreoffice_mcp.py
  * **Lazy UNO** — `initialize` and `tools/list` work with no office running;
    a live LibreOffice (started with `--accept=socket,...;urp;`) is contacted
    only when a tool that touches a document is called.

It reuses the proven UNO helpers in ``src/uno_bridge.py``.

NB: nothing may be printed to stdout except protocol messages — logs go to stderr.
"""

import json
import os
import sys

SERVER_NAME = "libreoffice"
SERVER_VERSION = "0.2.0"
DEFAULT_PROTOCOL = "2024-11-05"

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "src")


def _log(message):
    sys.stderr.write("[libreoffice-mcp] %s\n" % message)
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# Lazy LibreOffice connection (reuses src/uno_bridge.py)
# --------------------------------------------------------------------------- #

_state = {"ctx": None, "smgr": None, "desktop": None}


def _bridge():
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    import uno_bridge  # noqa: E402 - lazy; needs the `uno` runtime
    return uno_bridge


def _connect():
    if _state["desktop"] is None:
        ub = _bridge()
        port = int(os.environ.get("LO_UNO_PORT", "2002"))
        _log("connecting to LibreOffice on port %d ..." % port)
        ctx, smgr, desktop = ub.connect(port=port, retries=8, delay=0.5)
        _state.update(ctx=ctx, smgr=smgr, desktop=desktop)
    return _state


def _desktop():
    return _connect()["desktop"]


def _is_office_doc(comp):
    """A real document model — filters out the Start Center / Basic IDE, which
    also appear among the desktop's components and can even be 'current'."""
    try:
        return bool(comp) and comp.supportsService(
            "com.sun.star.document.OfficeDocument")
    except Exception:
        return False


def _open_docs():
    docs = []
    enum = _desktop().getComponents().createEnumeration()
    while enum.hasMoreElements():
        comp = enum.nextElement()
        if _is_office_doc(comp):
            docs.append(comp)
    return docs


def _current_doc():
    doc = _desktop().getCurrentComponent()
    if _is_office_doc(doc):
        return doc
    # Headless / unfocused sessions have no "current" component (or report the
    # Start Center) even when documents are open; fall back to the open list.
    docs = _open_docs()
    if len(docs) == 1:
        return docs[0]
    if len(docs) > 1:
        raise RuntimeError(
            "%d documents are open but none is focused; focus the one to "
            "act on (or close the others)." % len(docs))
    raise RuntimeError("No document is currently open/active in LibreOffice.")


def _require_calc():
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_calc(doc):
        raise RuntimeError("The active document is not a Calc spreadsheet.")
    return doc


def _require_writer():
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_writer(doc):
        raise RuntimeError("The active document is not a Writer document.")
    return doc


def _resolve_sheet(doc, sheet):
    sheets = doc.getSheets()
    if sheet is None or sheet == "":
        return doc.getCurrentController().getActiveSheet()
    if isinstance(sheet, int):
        return sheets.getByIndex(sheet)
    return sheets.getByName(str(sheet))


# --------------------------------------------------------------------------- #
# Small UNO helpers (all lazy — no top-level uno import)
# --------------------------------------------------------------------------- #

def _pv(name, value):
    from com.sun.star.beans import PropertyValue
    p = PropertyValue()
    p.Name = name
    p.Value = value
    return p


def _to_url(path):
    import unohelper
    return unohelper.systemPathToFileUrl(os.path.abspath(path))


def _uno_enum(type_name, value_name):
    import uno
    return uno.Enum(type_name, value_name)


def _uno_struct(type_name):
    import uno
    return uno.createUnoStruct(type_name)


def _hex_color(value):
    """'#RRGGBB' (or 'RRGGBB') -> int, as UNO colors are plain ints."""
    s = str(value).lstrip("#")
    if len(s) != 6:
        raise RuntimeError("Colors must be '#RRGGBB', got: %r" % value)
    return int(s, 16)


def _col_letters(index):
    s = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        s = chr(65 + rem) + s
    return s


def _addr_to_a1(addr):
    return "%s%d:%s%d" % (_col_letters(addr.StartColumn), addr.StartRow + 1,
                          _col_letters(addr.EndColumn), addr.EndRow + 1)


def _doc_kind(doc):
    ub = _bridge()
    return ("calc" if ub.is_calc(doc)
            else "writer" if ub.is_writer(doc) else "other")


def _doc_info(doc):
    try:
        title = doc.getTitle()
    except Exception:
        title = doc.getURL() if hasattr(doc, "getURL") else "?"
    return {"title": title, "type": _doc_kind(doc),
            "url": doc.getURL() if hasattr(doc, "getURL") else ""}


def _writer_end_cursor(doc):
    text = doc.getText()
    return text, text.createTextCursorByRange(text.getEnd())


def _append_paragraph(doc, style=None):
    """Add a paragraph break at the end (unless the doc is empty) and return a
    cursor in the new last paragraph, with an optional paragraph style."""
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    text, cursor = _writer_end_cursor(doc)
    if text.getString() != "":
        text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
        cursor.collapseToEnd()
    cursor.ParaStyleName = style if style else "Standard"
    return text, cursor


# --------------------------------------------------------------------------- #
# Tools — status & selection
# --------------------------------------------------------------------------- #

def tool_lo_status(_args):
    return {"connected": True,
            "documents": [_doc_info(doc) for doc in _open_docs()]}


def tool_list_documents(_args):
    return tool_lo_status(_args)


def tool_get_current_selection(_args):
    ub = _bridge()
    doc = _current_doc()
    if ub.is_calc(doc):
        rng = ub.get_calc_selection_range(doc)
        if rng is None:
            return {"type": "calc", "selection": None}
        addr = rng.getRangeAddress()
        return {"type": "calc",
                "range": {"sheet": addr.Sheet,
                          "startColumn": addr.StartColumn, "startRow": addr.StartRow,
                          "endColumn": addr.EndColumn, "endRow": addr.EndRow},
                "cells": ub.read_range_grid(rng)}
    if ub.is_writer(doc):
        text, has_selection = ub.get_writer_selection(doc)
        return {"type": "writer", "hasSelection": has_selection, "text": text}
    return {"type": "other"}


# --------------------------------------------------------------------------- #
# Tools — document lifecycle
# --------------------------------------------------------------------------- #

_FACTORY_URLS = {"calc": "private:factory/scalc",
                 "writer": "private:factory/swriter"}

# (doc kind, format) -> LibreOffice filter name
_FILTERS = {
    ("calc", "native"): "calc8",
    ("calc", "ods"): "calc8",
    ("calc", "xlsx"): "Calc MS Excel 2007 XML",
    ("calc", "csv"): "Text - txt - csv (StarCalc)",
    ("calc", "pdf"): "calc_pdf_Export",
    ("writer", "native"): "writer8",
    ("writer", "odt"): "writer8",
    ("writer", "docx"): "MS Word 2007 XML",
    ("writer", "txt"): "Text",
    ("writer", "pdf"): "writer_pdf_Export",
}


def tool_create_document(args):
    kind = args.get("type", "calc")
    url = _FACTORY_URLS.get(kind)
    if url is None:
        raise RuntimeError("type must be 'calc' or 'writer', got: %r" % kind)
    doc = _desktop().loadComponentFromURL(url, "_blank", 0, ())
    return {"created": _doc_info(doc)}


def tool_open_document(args):
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("File not found: %s" % path)
    doc = _desktop().loadComponentFromURL(_to_url(path), "_blank", 0, ())
    if doc is None:
        raise RuntimeError("LibreOffice could not open: %s" % path)
    return {"opened": _doc_info(doc)}


def tool_save_document(args):
    doc = _current_doc()
    kind = _doc_kind(doc)
    if kind == "other":
        raise RuntimeError("The active component is not a saveable document.")
    path = args.get("path")
    fmt = args.get("format")
    if not fmt:
        ext = os.path.splitext(path)[1].lstrip(".").lower() if path else ""
        fmt = ext if (kind, ext) in _FILTERS else "native"
    filt = _FILTERS.get((kind, fmt))
    if filt is None:
        raise RuntimeError("Unsupported format %r for a %s document. Choose "
                           "from: %s" % (fmt, kind,
                                         sorted(f for k, f in _FILTERS if k == kind)))
    if fmt == "pdf":
        if not path:
            raise RuntimeError("PDF export needs a 'path'.")
        doc.storeToURL(_to_url(path), (_pv("FilterName", filt),))
        return {"exported": os.path.abspath(path), "filter": filt}
    if path:
        doc.storeAsURL(_to_url(path),
                       (_pv("FilterName", filt), _pv("Overwrite", True)))
        return {"saved": os.path.abspath(path), "filter": filt}
    if not doc.hasLocation():
        raise RuntimeError("Document was never saved — provide a 'path'.")
    doc.store()
    return {"saved": doc.getURL(), "filter": "current"}


def tool_close_document(args):
    doc = _current_doc()
    info = _doc_info(doc)
    if args.get("save"):
        if not doc.hasLocation():
            raise RuntimeError("Document has no file yet — use save_document "
                               "with a 'path' first.")
        doc.store()
    doc.close(False)
    return {"closed": info}


# --------------------------------------------------------------------------- #
# Tools — Calc data
# --------------------------------------------------------------------------- #

def tool_calc_read_range(args):
    ub = _bridge()
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    return {"range": args["range"], "cells": ub.read_range_grid(rng)}


def _check_grid_shape(rng, grid, what):
    addr = rng.getRangeAddress()
    rows = addr.EndRow - addr.StartRow + 1
    cols = addr.EndColumn - addr.StartColumn + 1
    if len(grid) != rows or any(len(r) != cols for r in grid):
        raise RuntimeError(
            "%s shape %dx%d does not match the range (%dx%d)."
            % (what, len(grid), len(grid[0]) if grid else 0, rows, cols))
    return rows, cols


def tool_calc_write_range(args):
    ub = _bridge()
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    rows, cols = _check_grid_shape(rng, args["cells"], "cells")
    ub.write_range_grid(rng, args["cells"])
    return {"written": args["range"], "rows": rows, "columns": cols}


def tool_calc_get_formulas(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    return {"range": args["range"],
            "formulas": [list(row) for row in rng.getFormulaArray()]}


def tool_calc_set_formulas(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    formulas = args["formulas"]
    rows, cols = _check_grid_shape(rng, formulas, "formulas")
    rng.setFormulaArray(tuple(tuple("" if v is None else str(v) for v in row)
                              for row in formulas))
    return {"written": args["range"], "rows": rows, "columns": cols}


def tool_calc_clear_range(args):
    from com.sun.star.sheet.CellFlags import (VALUE, DATETIME, STRING, FORMULA,
                                              HARDATTR, STYLES)
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    flags = VALUE | DATETIME | STRING | FORMULA
    if args.get("include_formatting"):
        flags |= HARDATTR | STYLES
    rng.clearContents(flags)
    return {"cleared": args["range"],
            "formatting_cleared": bool(args.get("include_formatting"))}


def tool_calc_copy_range(args):
    doc = _require_calc()
    src_sheet = _resolve_sheet(doc, args.get("sheet"))
    dst_sheet = (_resolve_sheet(doc, args["target_sheet"])
                 if args.get("target_sheet") not in (None, "")
                 else src_sheet)
    src = src_sheet.getCellRangeByName(args["source_range"]).getRangeAddress()
    tgt = dst_sheet.getCellRangeByName(args["target_cell"]).getRangeAddress()
    dest = _uno_struct("com.sun.star.table.CellAddress")
    dest.Sheet = tgt.Sheet
    dest.Column = tgt.StartColumn
    dest.Row = tgt.StartRow
    src_sheet.copyRange(dest, src)
    return {"copied": args["source_range"], "to": args["target_cell"]}


def tool_calc_find_replace(args):
    doc = _require_calc()
    sheets = ([_resolve_sheet(doc, args["sheet"])]
              if args.get("sheet") not in (None, "")
              else [doc.getSheets().getByIndex(i)
                    for i in range(doc.getSheets().getCount())])
    total = 0
    for sheet in sheets:
        desc = sheet.createReplaceDescriptor()
        desc.SearchString = args["search"]
        desc.ReplaceString = args.get("replace", "")
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        desc.setPropertyValue("SearchWords", bool(args.get("whole_cells", False)))
        total += sheet.replaceAll(desc)
    return {"replacements": total, "sheets_searched": len(sheets)}


def tool_calc_get_used_range(args):
    ub = _bridge()
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    cursor = sheet.createCursor()
    cursor.gotoStartOfUsedArea(False)
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.getRangeAddress()
    return {"sheet": sheet.getName(),
            "range": _addr_to_a1(addr),
            "rows": addr.EndRow - addr.StartRow + 1,
            "columns": addr.EndColumn - addr.StartColumn + 1,
            "cells": ub.read_range_grid(cursor) if args.get("include_data")
                     else None}


def tool_calc_insert_rows(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    sheet.getRows().insertByIndex(int(args["index"]), int(args.get("count", 1)))
    return {"inserted_rows": int(args.get("count", 1)), "at_index": int(args["index"])}


def tool_calc_delete_rows(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    sheet.getRows().removeByIndex(int(args["index"]), int(args.get("count", 1)))
    return {"deleted_rows": int(args.get("count", 1)), "at_index": int(args["index"])}


def tool_calc_insert_columns(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    sheet.getColumns().insertByIndex(int(args["index"]), int(args.get("count", 1)))
    return {"inserted_columns": int(args.get("count", 1)), "at_index": int(args["index"])}


def tool_calc_delete_columns(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    sheet.getColumns().removeByIndex(int(args["index"]), int(args.get("count", 1)))
    return {"deleted_columns": int(args.get("count", 1)), "at_index": int(args["index"])}


# --------------------------------------------------------------------------- #
# Tools — Calc sheet management
# --------------------------------------------------------------------------- #

def tool_calc_list_sheets(_args):
    doc = _require_calc()
    sheets = doc.getSheets()
    active = doc.getCurrentController().getActiveSheet().getName()
    return {"sheets": list(sheets.getElementNames()), "active": active}


def tool_calc_add_sheet(args):
    doc = _require_calc()
    sheets = doc.getSheets()
    name = args["name"]
    if sheets.hasByName(name):
        raise RuntimeError("A sheet named %r already exists." % name)
    position = args.get("position")
    sheets.insertNewByName(name, int(position) if position is not None
                           else sheets.getCount())
    return {"added": name}


def tool_calc_delete_sheet(args):
    doc = _require_calc()
    sheets = doc.getSheets()
    name = args["name"]
    if not sheets.hasByName(name):
        raise RuntimeError("No sheet named %r." % name)
    if sheets.getCount() == 1:
        raise RuntimeError("Cannot delete the only sheet in the document.")
    sheets.removeByName(name)
    return {"deleted": name}


def tool_calc_rename_sheet(args):
    doc = _require_calc()
    sheets = doc.getSheets()
    name = args["name"]
    if not sheets.hasByName(name):
        raise RuntimeError("No sheet named %r." % name)
    sheets.getByName(name).setName(args["new_name"])
    return {"renamed": name, "to": args["new_name"]}


# --------------------------------------------------------------------------- #
# Tools — Calc formatting / presentation
# --------------------------------------------------------------------------- #

_H_ALIGN = {"left": "LEFT", "center": "CENTER", "right": "RIGHT",
            "justify": "BLOCK", "default": "STANDARD"}


def tool_calc_format_range(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    applied = []

    if "bold" in args:
        rng.CharWeight = 150.0 if args["bold"] else 100.0
        applied.append("bold")
    if "italic" in args:
        rng.CharPosture = _uno_enum("com.sun.star.awt.FontSlant",
                                    "ITALIC" if args["italic"] else "NONE")
        applied.append("italic")
    if "underline" in args:
        rng.CharUnderline = 1 if args["underline"] else 0
        applied.append("underline")
    if "font_name" in args:
        rng.CharFontName = args["font_name"]
        applied.append("font_name")
    if "font_size" in args:
        rng.CharHeight = float(args["font_size"])
        applied.append("font_size")
    if "font_color" in args:
        rng.CharColor = _hex_color(args["font_color"])
        applied.append("font_color")
    if "background_color" in args:
        rng.CellBackColor = _hex_color(args["background_color"])
        applied.append("background_color")
    if "wrap_text" in args:
        rng.IsTextWrapped = bool(args["wrap_text"])
        applied.append("wrap_text")
    if "horizontal_align" in args:
        key = str(args["horizontal_align"]).lower()
        if key not in _H_ALIGN:
            raise RuntimeError("horizontal_align must be one of %s"
                               % sorted(_H_ALIGN))
        rng.HoriJustify = _uno_enum("com.sun.star.table.CellHoriJustify",
                                    _H_ALIGN[key])
        applied.append("horizontal_align")
    if "number_format" in args:
        formats = doc.getNumberFormats()
        locale = _uno_struct("com.sun.star.lang.Locale")
        key = formats.queryKey(args["number_format"], locale, False)
        if key == -1:
            key = formats.addNew(args["number_format"], locale)
        rng.NumberFormat = key
        applied.append("number_format")
    if args.get("auto_fit_columns"):
        cols = rng.getColumns()
        for i in range(cols.getCount()):
            cols.getByIndex(i).OptimalWidth = True
        applied.append("auto_fit_columns")

    if not applied:
        raise RuntimeError("No formatting property given. Supported: bold, "
                           "italic, underline, font_name, font_size, font_color, "
                           "background_color, wrap_text, horizontal_align, "
                           "number_format, auto_fit_columns.")
    return {"formatted": args["range"], "applied": applied}


def tool_calc_merge_cells(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    merge = bool(args.get("merge", True))
    rng.merge(merge)
    return {"range": args["range"], "merged": merge}


_CHART_DIAGRAMS = {
    "column": ("com.sun.star.chart.BarDiagram", True),
    "bar": ("com.sun.star.chart.BarDiagram", False),
    "line": ("com.sun.star.chart.LineDiagram", None),
    "pie": ("com.sun.star.chart.PieDiagram", None),
    "area": ("com.sun.star.chart.AreaDiagram", None),
    "scatter": ("com.sun.star.chart.XYDiagram", None),
}


def tool_calc_create_chart(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    name = args["name"]
    charts = sheet.getCharts()
    if charts.hasByName(name):
        raise RuntimeError("A chart named %r already exists on this sheet." % name)

    chart_type = str(args.get("chart_type", "column")).lower()
    if chart_type not in _CHART_DIAGRAMS:
        raise RuntimeError("chart_type must be one of %s"
                           % sorted(_CHART_DIAGRAMS))

    rect = _uno_struct("com.sun.star.awt.Rectangle")
    anchor = args.get("position_cell")
    if anchor:
        pos = sheet.getCellRangeByName(anchor).Position
        rect.X, rect.Y = pos.X, pos.Y
    else:
        rect.X, rect.Y = 8000, 500
    rect.Width = int(args.get("width_mm", 120)) * 100
    rect.Height = int(args.get("height_mm", 80)) * 100

    addr = sheet.getCellRangeByName(args["data_range"]).getRangeAddress()
    charts.addNewByName(name, rect, (addr,),
                        bool(args.get("first_row_as_labels", True)),
                        bool(args.get("first_column_as_labels", False)))

    service, vertical = _CHART_DIAGRAMS[chart_type]
    chart_doc = charts.getByName(name).getEmbeddedObject()
    if service != "com.sun.star.chart.BarDiagram" or vertical is not None:
        diagram = chart_doc.createInstance(service)
        chart_doc.setDiagram(diagram)
        if vertical is not None:
            chart_doc.getDiagram().Vertical = vertical
    if args.get("title"):
        chart_doc.getTitle().String = args["title"]
    return {"chart": name, "type": chart_type, "data_range": args["data_range"]}


def tool_calc_select_range(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    controller = doc.getCurrentController()
    controller.setActiveSheet(sheet)
    controller.select(sheet.getCellRangeByName(args["range"]))
    return {"selected": args["range"], "sheet": sheet.getName()}


# --------------------------------------------------------------------------- #
# Tools — Writer
# --------------------------------------------------------------------------- #

def tool_writer_get_text(_args):
    doc = _require_writer()
    return {"text": doc.getText().getString()}


def tool_writer_replace_selection(args):
    ub = _bridge()
    doc = _require_writer()
    text = args["text"]
    _t, has_selection = ub.get_writer_selection(doc)
    if has_selection:
        ub.replace_writer_selection(doc, text)
        return {"action": "replaced"}
    ub.insert_writer_at_caret(doc, text)
    return {"action": "inserted_at_caret"}


def tool_writer_append_text(args):
    ub = _bridge()
    doc = _require_writer()
    if bool(args.get("new_paragraph", True)):
        text, cursor = _append_paragraph(doc, style="Standard")
    else:
        text, cursor = _writer_end_cursor(doc)
    ub._insert_multiline(text, cursor, args["text"], False)
    return {"appended": len(args["text"])}


def tool_writer_insert_heading(args):
    doc = _require_writer()
    level = int(args.get("level", 1))
    if not 1 <= level <= 6:
        raise RuntimeError("level must be 1..6")
    text, cursor = _append_paragraph(doc, style="Heading %d" % level)
    text.insertString(cursor, args["text"], False)
    return {"heading": args["text"], "level": level}


def tool_writer_find_replace(args):
    doc = _require_writer()
    desc = doc.createReplaceDescriptor()
    desc.SearchString = args["search"]
    desc.ReplaceString = args.get("replace", "")
    desc.setPropertyValue("SearchCaseSensitive",
                          bool(args.get("match_case", False)))
    desc.setPropertyValue("SearchWords", bool(args.get("whole_words", False)))
    count = doc.replaceAll(desc)
    return {"replacements": count}


def tool_writer_format_text(args):
    doc = _require_writer()
    desc = doc.createSearchDescriptor()
    desc.SearchString = args["search"]
    desc.setPropertyValue("SearchCaseSensitive",
                          bool(args.get("match_case", False)))
    found = doc.findAll(desc)
    for i in range(found.getCount()):
        rng = found.getByIndex(i)
        if "bold" in args:
            rng.CharWeight = 150.0 if args["bold"] else 100.0
        if "italic" in args:
            rng.CharPosture = _uno_enum("com.sun.star.awt.FontSlant",
                                        "ITALIC" if args["italic"] else "NONE")
        if "underline" in args:
            rng.CharUnderline = 1 if args["underline"] else 0
        if "font_name" in args:
            rng.CharFontName = args["font_name"]
        if "font_size" in args:
            rng.CharHeight = float(args["font_size"])
        if "font_color" in args:
            rng.CharColor = _hex_color(args["font_color"])
    return {"matches_formatted": found.getCount()}


def tool_writer_insert_table(args):
    doc = _require_writer()
    rows, cols = int(args["rows"]), int(args["columns"])
    if rows < 1 or cols < 1:
        raise RuntimeError("rows and columns must be >= 1")
    data = args.get("data")
    if data is not None and (len(data) > rows or any(len(r) > cols for r in data)):
        raise RuntimeError("data is larger than the table (%dx%d)." % (rows, cols))

    table = doc.createInstance("com.sun.star.text.TextTable")
    table.initialize(rows, cols)
    text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, table, False)

    filled = 0
    if data:
        for r, row in enumerate(data):
            for c, value in enumerate(row):
                cell = table.getCellByPosition(c, r)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cell.setValue(float(value))
                else:
                    cell.setString("" if value is None else str(value))
                filled += 1
    return {"table": "%dx%d" % (rows, cols), "cells_filled": filled}


def tool_writer_insert_image(args):
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("Image file not found: %s" % path)
    doc = _require_writer()
    state = _connect()
    provider = state["smgr"].createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", state["ctx"])
    graphic = provider.queryGraphic((_pv("URL", _to_url(path)),))
    if graphic is None:
        raise RuntimeError("Could not load image: %s" % path)

    image = doc.createInstance("com.sun.star.text.TextGraphicObject")
    image.Graphic = graphic
    try:
        native = graphic.Size100thMM
        width = int(args["width_mm"]) * 100 if args.get("width_mm") else native.Width
        height = int(args["height_mm"]) * 100 if args.get("height_mm") else native.Height
    except Exception:
        width = int(args.get("width_mm", 30)) * 100
        height = int(args.get("height_mm", 30)) * 100
    image.Width = max(width, 100)
    image.Height = max(height, 100)

    text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, image, False)
    return {"inserted": os.path.basename(path),
            "width_mm": image.Width // 100, "height_mm": image.Height // 100}


def tool_writer_insert_page_break(_args):
    doc = _require_writer()
    _text, cursor = _append_paragraph(doc, style="Standard")
    cursor.BreakType = _uno_enum("com.sun.star.style.BreakType", "PAGE_BEFORE")
    return {"inserted": "page_break"}


def tool_writer_get_outline(_args):
    doc = _require_writer()
    outline = []
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        para = enum.nextElement()
        try:
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
            level = int(para.getPropertyValue("OutlineLevel"))
        except Exception:
            continue
        if level > 0:
            outline.append({"level": level, "text": para.getString()})
    return {"outline": outline}


# --------------------------------------------------------------------------- #
# Tool registry + JSON schemas
# --------------------------------------------------------------------------- #

TOOLS = {
    # status & selection
    "lo_status": tool_lo_status,
    "list_documents": tool_list_documents,
    "get_current_selection": tool_get_current_selection,
    # document lifecycle
    "create_document": tool_create_document,
    "open_document": tool_open_document,
    "save_document": tool_save_document,
    "close_document": tool_close_document,
    # calc data
    "calc_read_range": tool_calc_read_range,
    "calc_write_range": tool_calc_write_range,
    "calc_get_formulas": tool_calc_get_formulas,
    "calc_set_formulas": tool_calc_set_formulas,
    "calc_clear_range": tool_calc_clear_range,
    "calc_copy_range": tool_calc_copy_range,
    "calc_find_replace": tool_calc_find_replace,
    "calc_get_used_range": tool_calc_get_used_range,
    "calc_insert_rows": tool_calc_insert_rows,
    "calc_delete_rows": tool_calc_delete_rows,
    "calc_insert_columns": tool_calc_insert_columns,
    "calc_delete_columns": tool_calc_delete_columns,
    # calc sheets
    "calc_list_sheets": tool_calc_list_sheets,
    "calc_add_sheet": tool_calc_add_sheet,
    "calc_delete_sheet": tool_calc_delete_sheet,
    "calc_rename_sheet": tool_calc_rename_sheet,
    # calc presentation
    "calc_format_range": tool_calc_format_range,
    "calc_merge_cells": tool_calc_merge_cells,
    "calc_create_chart": tool_calc_create_chart,
    "calc_select_range": tool_calc_select_range,
    # writer
    "writer_get_text": tool_writer_get_text,
    "writer_replace_selection": tool_writer_replace_selection,
    "writer_append_text": tool_writer_append_text,
    "writer_insert_heading": tool_writer_insert_heading,
    "writer_find_replace": tool_writer_find_replace,
    "writer_format_text": tool_writer_format_text,
    "writer_insert_table": tool_writer_insert_table,
    "writer_insert_image": tool_writer_insert_image,
    "writer_insert_page_break": tool_writer_insert_page_break,
    "writer_get_outline": tool_writer_get_outline,
}

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_RANGE = dict(_STR, description="A1 notation, e.g. 'A1:C10'")
_SHEET = {"description": "sheet name or 0-based index; omit for the active sheet"}
_GRID = {"type": "array", "items": {"type": "array"},
         "description": "rows of cell values (strings or numbers)"}


def _schema(props=None, required=None):
    schema = {"type": "object", "properties": props or {}}
    if required:
        schema["required"] = required
    return schema


TOOL_DEFS = [
    # --- status & selection ---
    {"name": "lo_status",
     "description": "Check the LibreOffice connection and list open documents.",
     "inputSchema": _schema()},
    {"name": "list_documents",
     "description": "List the documents currently open in LibreOffice.",
     "inputSchema": _schema()},
    {"name": "get_current_selection",
     "description": "Get the user's current selection: a Calc cell range (with data) or the selected Writer text.",
     "inputSchema": _schema()},
    # --- document lifecycle ---
    {"name": "create_document",
     "description": "Create and open a new empty document ('calc' spreadsheet or 'writer' text document).",
     "inputSchema": _schema({"type": dict(_STR, enum=["calc", "writer"])}, ["type"])},
    {"name": "open_document",
     "description": "Open a document file (ods/xlsx/csv/odt/docx/...) in LibreOffice.",
     "inputSchema": _schema({"path": dict(_STR, description="absolute or relative file path")}, ["path"])},
    {"name": "save_document",
     "description": "Save the active document. With 'path': save-as (format from extension or explicit 'format': ods/xlsx/csv/odt/docx/txt). 'format':'pdf' exports a PDF copy. Without 'path': save in place.",
     "inputSchema": _schema({"path": _STR,
                             "format": dict(_STR, enum=["native", "ods", "xlsx", "csv", "odt", "docx", "txt", "pdf"])})},
    {"name": "close_document",
     "description": "Close the active document, optionally saving it first (save=true needs an existing file location).",
     "inputSchema": _schema({"save": _BOOL})},
    # --- calc data ---
    {"name": "calc_read_range",
     "description": "Read a Calc cell range as a 2-D array of values.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "calc_write_range",
     "description": "Write a 2-D array of values into a Calc range (dimensions must match the range).",
     "inputSchema": _schema({"range": _RANGE, "cells": _GRID, "sheet": _SHEET}, ["range", "cells"])},
    {"name": "calc_get_formulas",
     "description": "Read a Calc range as formulas (e.g. '=SUM(A1:A3)') instead of computed values.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "calc_set_formulas",
     "description": "Write a 2-D array of formula strings (or literals) into a Calc range; dimensions must match.",
     "inputSchema": _schema({"range": _RANGE,
                             "formulas": dict(_GRID, description="rows of formula strings, e.g. [['=A1*2'], ['=A2*2']]"),
                             "sheet": _SHEET}, ["range", "formulas"])},
    {"name": "calc_clear_range",
     "description": "Clear the contents of a Calc range (values, text, formulas; optionally formatting too).",
     "inputSchema": _schema({"range": _RANGE, "include_formatting": _BOOL, "sheet": _SHEET}, ["range"])},
    {"name": "calc_copy_range",
     "description": "Copy a Calc range (values, formulas, formatting) to a target cell, optionally on another sheet.",
     "inputSchema": _schema({"source_range": _RANGE,
                             "target_cell": dict(_STR, description="top-left destination cell, e.g. 'E1'"),
                             "sheet": _SHEET,
                             "target_sheet": {"description": "destination sheet; defaults to the source sheet"}},
                            ["source_range", "target_cell"])},
    {"name": "calc_find_replace",
     "description": "Find & replace cell text in one sheet, or in every sheet when 'sheet' is omitted. Returns the replacement count.",
     "inputSchema": _schema({"search": _STR, "replace": _STR, "sheet": _SHEET,
                             "match_case": _BOOL,
                             "whole_cells": dict(_BOOL, description="match entire cell content only")},
                            ["search"])},
    {"name": "calc_get_used_range",
     "description": "Get the used (non-empty) area of a sheet as an A1 range with its size; optionally include the data.",
     "inputSchema": _schema({"sheet": _SHEET, "include_data": _BOOL})},
    {"name": "calc_insert_rows",
     "description": "Insert empty rows at a 0-based row index (existing rows shift down).",
     "inputSchema": _schema({"index": _INT, "count": _INT, "sheet": _SHEET}, ["index"])},
    {"name": "calc_delete_rows",
     "description": "Delete rows starting at a 0-based row index.",
     "inputSchema": _schema({"index": _INT, "count": _INT, "sheet": _SHEET}, ["index"])},
    {"name": "calc_insert_columns",
     "description": "Insert empty columns at a 0-based column index (existing columns shift right).",
     "inputSchema": _schema({"index": _INT, "count": _INT, "sheet": _SHEET}, ["index"])},
    {"name": "calc_delete_columns",
     "description": "Delete columns starting at a 0-based column index.",
     "inputSchema": _schema({"index": _INT, "count": _INT, "sheet": _SHEET}, ["index"])},
    # --- calc sheets ---
    {"name": "calc_list_sheets",
     "description": "List the sheet names of the active spreadsheet and which one is active.",
     "inputSchema": _schema()},
    {"name": "calc_add_sheet",
     "description": "Add a new sheet, optionally at a 0-based position (default: at the end).",
     "inputSchema": _schema({"name": _STR, "position": _INT}, ["name"])},
    {"name": "calc_delete_sheet",
     "description": "Delete a sheet by name (refuses to delete the last remaining sheet).",
     "inputSchema": _schema({"name": _STR}, ["name"])},
    {"name": "calc_rename_sheet",
     "description": "Rename a sheet.",
     "inputSchema": _schema({"name": _STR, "new_name": _STR}, ["name", "new_name"])},
    # --- calc presentation ---
    {"name": "calc_format_range",
     "description": "Format a Calc range: bold/italic/underline, font name/size/color, background color, wrap, horizontal alignment, number format code (e.g. '0.00%', '#,##0.00'), auto-fit columns.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "bold": _BOOL, "italic": _BOOL, "underline": _BOOL,
                             "font_name": _STR, "font_size": _NUM,
                             "font_color": dict(_STR, description="'#RRGGBB'"),
                             "background_color": dict(_STR, description="'#RRGGBB'"),
                             "wrap_text": _BOOL,
                             "horizontal_align": dict(_STR, enum=["left", "center", "right", "justify", "default"]),
                             "number_format": dict(_STR, description="LibreOffice number format code"),
                             "auto_fit_columns": _BOOL}, ["range"])},
    {"name": "calc_merge_cells",
     "description": "Merge (merge=true, default) or unmerge (merge=false) a Calc range.",
     "inputSchema": _schema({"range": _RANGE, "merge": _BOOL, "sheet": _SHEET}, ["range"])},
    {"name": "calc_create_chart",
     "description": "Create an embedded chart from a data range. Types: column, bar, line, pie, area, scatter.",
     "inputSchema": _schema({"name": dict(_STR, description="unique chart name on the sheet"),
                             "data_range": _RANGE,
                             "chart_type": dict(_STR, enum=["column", "bar", "line", "pie", "area", "scatter"]),
                             "position_cell": dict(_STR, description="cell the chart's top-left is anchored at, e.g. 'E2'"),
                             "width_mm": _INT, "height_mm": _INT,
                             "title": _STR,
                             "first_row_as_labels": _BOOL,
                             "first_column_as_labels": _BOOL,
                             "sheet": _SHEET},
                            ["name", "data_range"])},
    {"name": "calc_select_range",
     "description": "Select a range in the LibreOffice window (activates the sheet and highlights the range for the user).",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    # --- writer ---
    {"name": "writer_get_text",
     "description": "Get the full body text of the active Writer document.",
     "inputSchema": _schema()},
    {"name": "writer_replace_selection",
     "description": "Replace the current Writer selection with text (or insert at the caret if nothing is selected).",
     "inputSchema": _schema({"text": _STR}, ["text"])},
    {"name": "writer_append_text",
     "description": "Append text at the end of the Writer document ('\\n' becomes a paragraph break). new_paragraph=false continues the last paragraph.",
     "inputSchema": _schema({"text": _STR, "new_paragraph": _BOOL}, ["text"])},
    {"name": "writer_insert_heading",
     "description": "Append a heading paragraph (styles 'Heading 1'..'Heading 6') at the end of the document.",
     "inputSchema": _schema({"text": _STR, "level": dict(_INT, minimum=1, maximum=6)}, ["text"])},
    {"name": "writer_find_replace",
     "description": "Find & replace text across the Writer document. Returns the replacement count.",
     "inputSchema": _schema({"search": _STR, "replace": _STR,
                             "match_case": _BOOL, "whole_words": _BOOL}, ["search"])},
    {"name": "writer_format_text",
     "description": "Apply character formatting (bold/italic/underline/font/size/color) to every match of a search string.",
     "inputSchema": _schema({"search": _STR, "match_case": _BOOL,
                             "bold": _BOOL, "italic": _BOOL, "underline": _BOOL,
                             "font_name": _STR, "font_size": _NUM,
                             "font_color": dict(_STR, description="'#RRGGBB'")}, ["search"])},
    {"name": "writer_insert_table",
     "description": "Insert a table at the end of the Writer document, optionally filled with data (rows of strings/numbers).",
     "inputSchema": _schema({"rows": _INT, "columns": _INT, "data": _GRID}, ["rows", "columns"])},
    {"name": "writer_insert_image",
     "description": "Insert an image file at the end of the Writer document (size in mm; defaults to the image's own size).",
     "inputSchema": _schema({"path": _STR, "width_mm": _INT, "height_mm": _INT}, ["path"])},
    {"name": "writer_insert_page_break",
     "description": "Insert a page break at the end of the Writer document.",
     "inputSchema": _schema()},
    {"name": "writer_get_outline",
     "description": "List the document's headings as an outline: [{level, text}, ...].",
     "inputSchema": _schema()},
]


# --------------------------------------------------------------------------- #
# JSON-RPC / MCP plumbing
# --------------------------------------------------------------------------- #

def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(message):
    method = message.get("method")
    mid = message.get("id")

    if method == "initialize":
        params = message.get("params") or {}
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return _result(mid, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "notifications/initialized":
        return None  # notification, no reply
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": TOOL_DEFS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        func = TOOLS.get(name)
        if func is None:
            return _error(mid, -32602, "Unknown tool: %s" % name)
        try:
            payload = func(args)
            text = json.dumps(payload, ensure_ascii=False)
            return _result(mid, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:  # tool errors are reported in-band, not as JSON-RPC errors
            return _result(mid, {"content": [{"type": "text", "text": "Error: %s" % exc}],
                                 "isError": True})

    if mid is not None:
        return _error(mid, -32601, "Unknown method: %s" % method)
    return None  # unknown notification


def main():
    _log("LibreOffice MCP server ready (stdio, %d tools). LO_UNO_PORT=%s"
         % (len(TOOLS), os.environ.get("LO_UNO_PORT", "2002")))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _log("ignoring non-JSON line")
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
