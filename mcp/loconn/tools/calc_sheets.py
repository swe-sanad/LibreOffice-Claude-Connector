# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Calc tools — sheets."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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


def tool_calc_set_active_sheet(args):
    """Activate a sheet in the UI and optionally select/scroll to a cell —
    select() alone does not scroll the viewport."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    ctrl = doc.getCurrentController()
    ctrl.setActiveSheet(sheet)
    cell = args.get("cell")
    if cell:
        rng = sheet.getCellRangeByName(cell)
        ctrl.select(rng)
        try:
            addr = rng.getRangeAddress()
            ctrl.setFirstVisibleColumn(max(0, addr.StartColumn))
            ctrl.setFirstVisibleRow(max(0, addr.StartRow))
        except Exception:
            pass
    return {"active": sheet.Name, "selected": cell}


def tool_calc_sheet_properties(args):
    """Read (and optionally set) per-sheet properties: rtl (TableLayout — set
    BEFORE placing shapes!), visible, freeze rows/cols."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    changed = {}
    if args.get("rtl") is not None:
        sheet.TableLayout = 1 if args["rtl"] else 0
        changed["rtl"] = bool(args["rtl"])
    if args.get("visible") is not None:
        sheet.IsVisible = bool(args["visible"])
        changed["visible"] = bool(args["visible"])
    if args.get("freeze_rows") is not None or args.get("freeze_cols") is not None:
        ctrl = doc.getCurrentController()
        prev = ctrl.getActiveSheet()
        ctrl.setActiveSheet(sheet)
        ctrl.freezeAtPosition(int(args.get("freeze_cols") or 0),
                              int(args.get("freeze_rows") or 0))
        ctrl.setActiveSheet(prev)
        changed["freeze"] = [int(args.get("freeze_cols") or 0),
                             int(args.get("freeze_rows") or 0)]
    return {"sheet": sheet.Name, "rtl": sheet.TableLayout == 1,
            "visible": bool(sheet.IsVisible), "changed": changed}


def _calc_axis(sheet, axis):
    """'columns'|'rows' -> the sheet's column/row collection. Raises on typos."""
    a = str(axis).lower()
    if a in ("columns", "column", "col", "cols"):
        return sheet.getColumns(), "columns"
    if a in ("rows", "row"):
        return sheet.getRows(), "rows"
    raise RuntimeError("axis must be 'columns' or 'rows', got: %r" % axis)


def tool_calc_set_dimensions(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    coll, axis = _calc_axis(sheet, args["axis"])
    start = int(args["start"])
    count = int(args.get("count", 1))
    autofit = bool(args.get("autofit", False))
    size_mm = args.get("size_mm")
    if not autofit and size_mm is None:
        raise RuntimeError("Provide 'size_mm' or set 'autofit': true.")
    for i in range(start, start + count):
        item = coll.getByIndex(i)
        if autofit:
            if axis == "columns":
                item.OptimalWidth = True
            else:
                item.OptimalHeight = True
        else:
            v = _mm100(size_mm)
            if axis == "columns":
                item.Width = v
            else:
                item.Height = v
    return {"axis": axis, "start": start, "count": count,
            "autofit": autofit, "size_mm": None if autofit else size_mm}


def tool_calc_set_visibility(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    coll, axis = _calc_axis(sheet, args["axis"])
    start = int(args["start"])
    count = int(args.get("count", 1))
    visible = bool(args["visible"])
    for i in range(start, start + count):
        coll.getByIndex(i).IsVisible = visible
    return {"axis": axis, "start": start, "count": count, "visible": visible}


def tool_calc_move_sheet(args):
    doc = _require_calc()
    sheets = doc.getSheets()
    name = args["name"]
    if not sheets.hasByName(name):
        raise RuntimeError("No sheet named %r. Sheets: %s"
                           % (name, ", ".join(sheets.getElementNames())))
    position = int(args["position"])
    sheets.moveByName(name, position)
    return {"moved": name, "to_position": position,
            "order": list(sheets.getElementNames())}


def tool_calc_named_ranges(args):
    doc = _require_calc()
    names = doc.NamedRanges
    action = str(args.get("action", "list")).lower()
    if action == "list":
        out = []
        for nm in names.getElementNames():
            try:
                out.append({"name": nm, "content": names.getByName(nm).getContent()})
            except Exception:
                out.append({"name": nm})
        return {"named_ranges": out}
    if action == "add":
        name, content = args["name"], args["content"]
        sheet = _resolve_sheet(doc, args.get("sheet"))
        ref = _uno_struct("com.sun.star.table.CellAddress")
        ref.Sheet = sheet.getRangeAddress().Sheet
        ref.Column = 0
        ref.Row = 0
        names.addNewByName(name, content, ref, 0)
        return {"added": name, "content": content}
    if action == "delete":
        name = args["name"]
        if not names.hasByName(name):
            raise RuntimeError("No named range %r." % name)
        names.removeByName(name)
        return {"deleted": name}
    raise RuntimeError("action must be list|add|delete.")


def tool_calc_page_setup(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    ps = doc.getStyleFamilies().getByName("PageStyles").getByName(sheet.PageStyle)
    changed = []
    if args.get("landscape") is not None:
        ps.IsLandscape = bool(args["landscape"])
        changed.append("landscape")
    if args.get("paper"):
        size = _PAPER.get(str(args["paper"]).lower())
        if size:
            w, h = size
            if getattr(ps, "IsLandscape", False):
                w, h = h, w
            s = _uno_struct("com.sun.star.awt.Size")
            s.Width, s.Height = w, h
            ps.Size = s
            changed.append("paper")
    for key, prop in (("margin_top", "TopMargin"), ("margin_bottom", "BottomMargin"),
                      ("margin_left", "LeftMargin"), ("margin_right", "RightMargin")):
        if args.get(key) is not None:
            setattr(ps, prop, _mm100(args[key]))
            changed.append(key)
    for key, prop in (("scale", "PageScale"), ("fit_pages_x", "ScaleToPagesX"),
                      ("fit_pages_y", "ScaleToPagesY")):
        if args.get(key) is not None:
            setattr(ps, prop, int(args[key]))
            changed.append(key)
    if args.get("center_h") is not None:
        ps.CenterHorizontally = bool(args["center_h"])
        changed.append("center_h")
    if args.get("center_v") is not None:
        ps.CenterVertically = bool(args["center_v"])
        changed.append("center_v")
    return {"page_style": sheet.PageStyle, "changed": changed}


def tool_calc_set_print_area(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    if args.get("clear"):
        sheet.setPrintAreas(())
        return {"print_area": "cleared"}
    addr = sheet.getCellRangeByName(args["range"]).getRangeAddress()
    sheet.setPrintAreas((addr,))
    if args.get("title_rows"):
        sheet.setTitleRows(sheet.getCellRangeByName(args["title_rows"]).getRangeAddress())
        sheet.setPrintTitleRows(True)
    if args.get("title_columns"):
        sheet.setTitleColumns(sheet.getCellRangeByName(args["title_columns"]).getRangeAddress())
        sheet.setPrintTitleColumns(True)
    return {"print_area": args["range"]}


def tool_calc_group_outline(args):
    from com.sun.star.table.TableOrientation import ROWS, COLUMNS
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    orient = (COLUMNS if str(args.get("axis", "rows")).lower().startswith("col")
              else ROWS)
    action = str(args.get("action", "group")).lower()
    if action == "clear":
        sheet.clearOutline()
        return {"outline": "cleared"}
    addr = sheet.getCellRangeByName(args["range"]).getRangeAddress()
    if action == "group":
        sheet.group(addr, orient)
    elif action == "ungroup":
        sheet.ungroup(addr, orient)
    elif action == "show":
        sheet.showDetail(addr)
    elif action == "hide":
        sheet.hideDetail(addr)
    else:
        raise RuntimeError("action must be group|ungroup|show|hide|clear.")
    return {"outline": action, "range": args["range"]}


def tool_calc_copy_sheet(args):
    doc = _require_calc()
    sheets = doc.getSheets()
    src = args["name"]
    if not sheets.hasByName(src):
        raise RuntimeError("No sheet named %r." % src)
    dest = args["new_name"]
    if sheets.hasByName(dest):
        raise RuntimeError("A sheet named %r already exists." % dest)
    pos = args.get("position")
    sheets.copyByName(src, dest, int(pos) if pos is not None else sheets.getCount())
    return {"copied": src, "to": dest}


def tool_calc_set_document_defaults(args):
    """Set the workbook's base typography by editing the Default cell style —
    the Calc twin of writer_set_document_defaults. Applied to Western, Complex
    (RTL/CTL) and Asian scripts so an Arabic base font actually takes."""
    doc = _require_calc()
    default = doc.getStyleFamilies().getByName("CellStyles").getByName("Default")
    changed = []
    if args.get("font_name"):
        name = str(args["font_name"])
        for prop in ("CharFontName", "CharFontNameComplex", "CharFontNameAsian"):
            setattr(default, prop, name)
        changed.append("font_name")
    if args.get("font_size") is not None:
        size = float(args["font_size"])
        for prop in ("CharHeight", "CharHeightComplex", "CharHeightAsian"):
            setattr(default, prop, size)
        changed.append("font_size")
    if not changed:
        raise RuntimeError("Give font_name and/or font_size.")
    return {"style": "Default", "changed": changed,
            "note": "Cells carrying their own explicit formatting keep it; this "
                    "changes the baseline everything else inherits."}


def tool_calc_set_header_footer(args):
    """Page headers and footers for printing a spreadsheet — the Calc twin of
    writer_set_header_footer. Each has independent left/centre/right parts."""
    doc = _require_calc()
    which = str(args.get("which", "header")).lower()
    if which not in ("header", "footer"):
        raise RuntimeError("which must be 'header' or 'footer'.")

    sheet = (_resolve_sheet(doc, args["sheet"]) if args.get("sheet") not in (None, "")
             else doc.getCurrentController().getActiveSheet())
    style_name = args.get("style_name") or sheet.PageStyle
    page = doc.getStyleFamilies().getByName("PageStyles").getByName(style_name)

    enable = args.get("enable")
    if enable is not None:
        setattr(page, "HeaderIsOn" if which == "header" else "FooterIsOn",
                bool(enable))
    if enable is False:
        return {"page_style": style_name, "which": which, "enabled": False}
    # turning content on implicitly: writing to a disabled header does nothing
    setattr(page, "HeaderIsOn" if which == "header" else "FooterIsOn", True)
    setattr(page, "HeaderIsShared" if which == "header" else "FooterIsShared",
            bool(args.get("shared", True)))

    prop = ("RightPageHeaderContent" if which == "header"
            else "RightPageFooterContent")
    content = getattr(page, prop)
    parts = []
    for key, attr in (("left", "LeftText"), ("center", "CenterText"),
                      ("right", "RightText")):
        if args.get(key) is not None:
            getattr(content, attr).setString(str(args[key]))
            parts.append(key)
    if not parts:
        raise RuntimeError("Give at least one of left/center/right (or "
                           "enable=false to switch the %s off)." % which)
    setattr(page, prop, content)      # the struct must be written BACK
    if args.get("shared", True):      # keep left-hand pages in step
        try:
            setattr(page, prop.replace("Right", "Left"), content)
        except Exception:
            pass
    return {"page_style": style_name, "which": which, "enabled": True,
            "parts_set": parts, "shared": bool(args.get("shared", True))}


TOOL_DEFS = [
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
    {"name": "calc_set_active_sheet",
     "description": "Activate a sheet in the LibreOffice window and optionally select AND scroll to a cell (plain select() does not scroll the viewport).",
     "inputSchema": _schema({"sheet": _SHEET,
                             "cell": dict(_STR, description="cell to select+scroll to, e.g. 'A15'")})},
    {"name": "calc_sheet_properties",
     "description": "Read and optionally set per-sheet properties: rtl (right-to-left layout — set BEFORE placing shapes, coordinates mirror), visible (hide/show), freeze_rows/freeze_cols (frozen panes). Omitted properties are left unchanged; the reply reports the current state.",
     "inputSchema": _schema({"sheet": _SHEET, "rtl": _BOOL, "visible": _BOOL,
                             "freeze_rows": _INT, "freeze_cols": _INT})},
    {"name": "calc_set_dimensions",
     "description": "Set column widths or row heights (mm) or auto-fit them for a span. Give 'axis' ('columns'|'rows'), 'start' (0-based), 'count', and either 'size_mm' or 'autofit': true.",
     "inputSchema": _schema({"sheet": _SHEET,
                             "axis": dict(_STR, enum=["columns", "rows"]),
                             "start": _INT, "count": _INT,
                             "size_mm": dict(_NUM, description="width/height in mm"),
                             "autofit": dict(_BOOL, description="auto-fit instead of a fixed size")},
                            ["axis", "start"])},
    {"name": "calc_set_visibility",
     "description": "Hide or show a span of rows or columns. Give 'axis' ('columns'|'rows'), 'start' (0-based), 'count', and 'visible'.",
     "inputSchema": _schema({"sheet": _SHEET,
                             "axis": dict(_STR, enum=["columns", "rows"]),
                             "start": _INT, "count": _INT, "visible": _BOOL},
                            ["axis", "start", "visible"])},
    {"name": "calc_move_sheet",
     "description": "Reorder an existing sheet to a new 0-based position.",
     "inputSchema": _schema({"name": _STR, "position": _INT}, ["name", "position"])},
    {"name": "calc_set_document_defaults",
     "description": "Set the workbook's base font and size by editing the 'Default' cell style — the Calc twin of writer_set_document_defaults. Applied to Western, Complex (RTL/CTL) and Asian scripts together, so an Arabic base font actually takes effect. Cells with their own explicit formatting keep it.",
     "inputSchema": _schema({"font_name": _STR,
                             "font_size": dict(_NUM, description="points")})},
    {"name": "calc_set_header_footer",
     "description": "Set the printed page header or footer of a spreadsheet — the Calc twin of writer_set_header_footer. Each has independent left/center/right parts. Use enable=false to switch it off. Applies to the active sheet's page style unless 'sheet' or 'style_name' says otherwise.",
     "inputSchema": _schema({"which": dict(_STR, enum=["header", "footer"]),
                             "left": dict(_STR, description="left-hand text"),
                             "center": dict(_STR, description="centre text"),
                             "right": dict(_STR, description="right-hand text"),
                             "enable": dict(_BOOL, description="false switches it off"),
                             "shared": dict(_BOOL, description="same on left and right pages (default true)"),
                             "sheet": _SHEET,
                             "style_name": dict(_STR, description="page style name (default: the active sheet's)")})},
    {"name": "calc_named_ranges",
     "description": "Workbook named ranges: action 'list', 'add' (name + content like 'Sheet1.$A$1:$B$5'), or 'delete'.",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "add", "delete"]),
                             "name": _STR, "content": dict(_STR, description="the range reference"),
                             "sheet": _SHEET})},
    {"name": "calc_page_setup",
     "description": "Calc page style: landscape, paper (a4/a5/a3/letter/legal), margins (mm), scale %, fit_pages_x/y, center_h/center_v.",
     "inputSchema": _schema({"sheet": _SHEET, "landscape": _BOOL,
                             "paper": dict(_STR, enum=["a4", "a5", "a3", "letter", "legal"]),
                             "margin_top": _NUM, "margin_bottom": _NUM,
                             "margin_left": _NUM, "margin_right": _NUM,
                             "scale": _INT, "fit_pages_x": _INT, "fit_pages_y": _INT,
                             "center_h": _BOOL, "center_v": _BOOL})},
    {"name": "calc_set_print_area",
     "description": "Define the print range for a sheet (or clear=true), with optional repeating title_rows / title_columns ranges.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "clear": _BOOL,
                             "title_rows": _STR, "title_columns": _STR})},
    {"name": "calc_group_outline",
     "description": "Row/column outline: action group/ungroup/show/hide over a range (axis rows|columns), or clear the whole outline.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "action": dict(_STR, enum=["group", "ungroup", "show", "hide", "clear"]),
                             "axis": dict(_STR, enum=["rows", "columns"])})},
    {"name": "calc_copy_sheet",
     "description": "Duplicate a sheet within the document to 'new_name' at an optional 0-based position.",
     "inputSchema": _schema({"name": _STR, "new_name": _STR, "position": _INT},
                            ["name", "new_name"])},
]

register(globals(), TOOL_DEFS,
         basic=['calc_add_sheet', 'calc_list_sheets'],
         read_only=['calc_list_sheets', 'calc_set_active_sheet'])
