# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Calc tools — analysis."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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


def tool_calc_statistics(args):
    """Descriptive statistics over the NUMERIC cells in a Calc range: count, sum,
    mean, min, max, median, and population stdev. Text/empty cells are ignored."""
    from com.sun.star.table.CellContentType import VALUE, FORMULA
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    addr = rng.getRangeAddress()
    nums = []
    for r in range(addr.EndRow - addr.StartRow + 1):
        for c in range(addr.EndColumn - addr.StartColumn + 1):
            cell = rng.getCellByPosition(c, r)
            t = cell.getType()
            if t == VALUE or (t == FORMULA and not cell.getError()):
                nums.append(cell.getValue())
    if not nums:
        return {"range": args["range"], "count": 0}
    n = len(nums)
    total = sum(nums)
    mean = total / n
    srt = sorted(nums)
    median = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    stdev = (sum((x - mean) ** 2 for x in nums) / n) ** 0.5
    return {"range": args["range"], "count": n, "sum": total, "mean": mean,
            "min": min(nums), "max": max(nums), "median": median, "stdev": stdev}


def tool_calc_list_shapes(args):
    """Everything actually on a sheet's DrawPage — names, positions (mm), text,
    OnClick script, control-or-drawing. The tool that would have caught the
    RTL dropped-buttons bug in one call."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    dp = sheet.DrawPage
    shapes = []
    for i in range(dp.Count):
        shp = dp.getByIndex(i)
        info = {"index": i, "name": getattr(shp, "Name", "")}
        try:
            info["type"] = shp.ShapeType
        except Exception:
            pass
        try:
            info["position_mm"] = [round(shp.Position.X / 100.0, 1),
                                   round(shp.Position.Y / 100.0, 1)]
            info["size_mm"] = [round(shp.Size.Width / 100.0, 1),
                               round(shp.Size.Height / 100.0, 1)]
        except Exception:
            pass
        try:
            txt = shp.getString()
            if txt:
                info["text"] = txt[:80]
        except Exception:
            pass
        try:
            for p in shp.Events.getByName("OnClick"):
                if p.Name == "Script" and p.Value:
                    info["on_click"] = p.Value
        except Exception:
            pass
        try:
            info["is_form_control"] = bool(
                shp.supportsService("com.sun.star.drawing.ControlShape"))
        except Exception:
            pass
        shapes.append(info)
    return {"sheet": sheet.Name, "count": dp.Count, "shapes": shapes}


def tool_calc_delete_shape(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    dp = sheet.DrawPage
    name = args["name"]
    removed = 0
    for i in range(dp.Count - 1, -1, -1):
        shp = dp.getByIndex(i)
        if getattr(shp, "Name", "") == name:
            dp.remove(shp)
            removed += 1
    if not removed:
        raise RuntimeError("No shape named %r on sheet %s." % (name, sheet.Name))
    return {"removed": removed, "sheet": sheet.Name}


def tool_calc_delete_chart(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    charts = sheet.getCharts()
    name = args["name"]
    if not charts.hasByName(name):
        raise RuntimeError("No chart named %r on this sheet. Charts: %s"
                           % (name, ", ".join(charts.getElementNames())))
    charts.removeByName(name)
    return {"deleted_chart": name}


def tool_calc_add_shape(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    kind = str(args.get("kind", "rectangle")).lower()
    service = _CALC_SHAPES.get(kind)
    if not service:
        raise RuntimeError("kind must be one of %s." % sorted(_CALC_SHAPES))
    shape = doc.createInstance(service)
    sheet.DrawPage.add(shape)
    size = _uno_struct("com.sun.star.awt.Size")
    size.Width = _mm100(args.get("width_mm", 40))
    size.Height = _mm100(args.get("height_mm", 20))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
    if args.get("position_cell"):
        p = sheet.getCellRangeByName(args["position_cell"]).Position
        pos.X, pos.Y = p.X, p.Y
    else:
        pos.X = _mm100(args.get("x_mm", 10))
        pos.Y = _mm100(args.get("y_mm", 10))
    shape.setPosition(pos)
    if args.get("fill_color") is not None:
        shape.FillColor = _hex_color(args["fill_color"])
    if args.get("line_color") is not None:
        shape.LineColor = _hex_color(args["line_color"])
    if args.get("text"):
        shape.setString(args["text"])
    if args.get("name"):
        try:
            shape.Name = args["name"]
        except Exception:
            pass
    return {"added_shape": kind, "name": getattr(shape, "Name", "")}


def tool_calc_insert_image(args):
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("Image file not found: %s" % path)
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    state = _connect()
    provider = state["smgr"].createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", state["ctx"])
    graphic = provider.queryGraphic((_pv("URL", _to_url(path)),))
    if graphic is None:
        raise RuntimeError("Could not load image: %s" % path)
    shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
    shape.Graphic = graphic
    sheet.DrawPage.add(shape)
    size = _uno_struct("com.sun.star.awt.Size")
    try:
        native = graphic.Size100thMM
        size.Width = (_mm100(args["width_mm"]) if args.get("width_mm")
                      else native.Width or 4000)
        size.Height = (_mm100(args["height_mm"]) if args.get("height_mm")
                       else native.Height or 3000)
    except Exception:
        size.Width = _mm100(args.get("width_mm", 40))
        size.Height = _mm100(args.get("height_mm", 30))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
    if args.get("position_cell"):
        p = sheet.getCellRangeByName(args["position_cell"]).Position
        pos.X, pos.Y = p.X, p.Y
    else:
        pos.X = _mm100(args.get("x_mm", 5))
        pos.Y = _mm100(args.get("y_mm", 5))
    shape.setPosition(pos)
    return {"inserted": os.path.basename(path)}


def _find_shape(sheet, name):
    dp = sheet.DrawPage
    for i in range(dp.getCount()):
        shp = dp.getByIndex(i)
        if getattr(shp, "Name", None) == name:
            return shp
    return None


def tool_calc_position_shape(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    shape = _find_shape(sheet, args["name"])
    if shape is None:
        raise RuntimeError("No shape named %r on this sheet." % args["name"])
    if args.get("x_mm") is not None or args.get("y_mm") is not None:
        cur = shape.Position
        p = _uno_struct("com.sun.star.awt.Point")
        p.X = _mm100(args["x_mm"]) if args.get("x_mm") is not None else cur.X
        p.Y = _mm100(args["y_mm"]) if args.get("y_mm") is not None else cur.Y
        shape.setPosition(p)
    if args.get("width_mm") is not None or args.get("height_mm") is not None:
        cur = shape.Size
        s = _uno_struct("com.sun.star.awt.Size")
        s.Width = _mm100(args["width_mm"]) if args.get("width_mm") is not None else cur.Width
        s.Height = _mm100(args["height_mm"]) if args.get("height_mm") is not None else cur.Height
        shape.setSize(s)
    if args.get("z_order") is not None:
        try:
            shape.ZOrder = int(args["z_order"])
        except Exception:
            pass
    return {"positioned": args["name"]}


def tool_calc_edit_chart(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    charts = sheet.getCharts()
    name = args["name"]
    if not charts.hasByName(name):
        raise RuntimeError("No chart named %r. Charts: %s"
                           % (name, ", ".join(charts.getElementNames())))
    cdoc = charts.getByName(name).getEmbeddedObject()
    changed = []
    if args.get("title") is not None:
        cdoc.HasMainTitle = True
        cdoc.getTitle().String = args["title"]
        changed.append("title")
    if args.get("subtitle") is not None:
        cdoc.HasSubTitle = True
        cdoc.getSubTitle().String = args["subtitle"]
        changed.append("subtitle")
    if args.get("legend") is not None:
        cdoc.HasLegend = bool(args["legend"])
        changed.append("legend")
    if args.get("x_axis_title") is not None:
        diag = cdoc.getDiagram()
        diag.HasXAxisTitle = True
        diag.getXAxisTitle().String = args["x_axis_title"]
        changed.append("x_axis_title")
    if args.get("y_axis_title") is not None:
        diag = cdoc.getDiagram()
        diag.HasYAxisTitle = True
        diag.getYAxisTitle().String = args["y_axis_title"]
        changed.append("y_axis_title")
    if args.get("chart_type"):
        ct = str(args["chart_type"]).lower()
        if ct not in _CHART_DIAGRAMS:
            raise RuntimeError("chart_type must be one of %s."
                               % sorted(_CHART_DIAGRAMS))
        service, vertical = _CHART_DIAGRAMS[ct]
        cdoc.setDiagram(cdoc.createInstance(service))
        if vertical is not None:
            cdoc.getDiagram().Vertical = vertical
        changed.append("chart_type")
    return {"chart": name, "changed": changed}


def tool_calc_list_charts(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    charts = sheet.getCharts()
    out = []
    for nm in charts.getElementNames():
        c = charts.getByName(nm)
        entry = {"name": nm}
        try:
            entry["ranges"] = [_addr_to_a1(a) for a in c.getRanges()]
        except Exception:
            pass
        for key, prop in (("column_headers", "HasColumnHeaders"),
                          ("row_headers", "HasRowHeaders")):
            try:
                entry[key] = bool(getattr(c, prop))
            except Exception:
                pass
        out.append(entry)
    return {"charts": out}


def tool_calc_create_pivot(args):
    from com.sun.star.sheet.DataPilotFieldOrientation import ROW, COLUMN, PAGE, DATA
    from com.sun.star.sheet.GeneralFunction import (SUM, COUNT, AVERAGE, MAX, MIN,
                                                    PRODUCT, COUNTNUMS)
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    pilots = sheet.getDataPilotTables()
    name = args["name"]
    if pilots.hasByName(name):
        raise RuntimeError("A pivot table named %r already exists." % name)
    desc = pilots.createDataPilotDescriptor()
    src = sheet.getCellRangeByName(args["source_range"]).getRangeAddress()
    desc.setSourceRange(src)
    fields = desc.getDataPilotFields()
    byname = {}
    for i in range(fields.getCount()):
        f = fields.getByIndex(i)
        try:
            byname[f.Name] = f
        except Exception:
            pass
    orient_map = {"row": ROW, "column": COLUMN, "page": PAGE, "data": DATA}
    func_map = {"sum": SUM, "count": COUNT, "average": AVERAGE, "max": MAX,
                "min": MIN, "product": PRODUCT, "countnums": COUNTNUMS}
    for spec in (args.get("fields") or []):
        fname = spec["field"]
        f = byname.get(fname)
        if f is None:
            raise RuntimeError("No source field %r. Available: %s"
                               % (fname, ", ".join(byname)))
        orient = orient_map.get(str(spec.get("orientation", "row")).lower(), ROW)
        f.Orientation = orient
        if orient == DATA and spec.get("function"):
            f.Function = func_map.get(str(spec["function"]).lower(), SUM)
    out_addr = sheet.getCellRangeByName(args["output_cell"]).getRangeAddress()
    dest = _uno_struct("com.sun.star.table.CellAddress")
    dest.Sheet = out_addr.Sheet
    dest.Column = out_addr.StartColumn
    dest.Row = out_addr.StartRow
    pilots.insertNewByName(name, dest, desc)
    return {"pivot": name, "source": args["source_range"]}


def tool_calc_refresh_pivot(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    pilots = sheet.getDataPilotTables()
    action = str(args.get("action", "refresh")).lower()
    if action == "list":
        return {"pivots": list(pilots.getElementNames())}
    name = args.get("name")
    if action == "refresh":
        targets = [name] if name else list(pilots.getElementNames())
        for nm in targets:
            pilots.getByName(nm).refresh()
        return {"refreshed": name or "all"}
    if action == "delete":
        if not name:
            raise RuntimeError("delete needs 'name'.")
        pilots.removeByName(name)
        return {"deleted": name}
    raise RuntimeError("action must be list|refresh|delete.")


def tool_calc_add_subtotals(args):
    from com.sun.star.sheet.GeneralFunction import SUM, COUNT, AVERAGE, MAX, MIN
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    if args.get("remove"):
        rng.removeSubTotals()
        return {"subtotals": "removed"}
    func_map = {"sum": SUM, "count": COUNT, "average": AVERAGE, "max": MAX, "min": MIN}
    func = func_map.get(str(args.get("function", "sum")).lower(), SUM)
    fields = []
    for c in args["columns"]:
        col = _uno_struct("com.sun.star.sheet.SubTotalColumn")
        col.Column = int(c)
        col.Function = func
        fields.append(col)
    desc = rng.createSubTotalDescriptor(True)
    desc.addNew(tuple(fields), int(args["group_by"]))
    rng.applySubTotals(desc, bool(args.get("replace", True)))
    return {"subtotals": "applied", "group_by": int(args["group_by"])}


def tool_calc_group_shapes(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    dp = sheet.DrawPage
    if args.get("ungroup"):
        grp = _find_shape(sheet, args["group"])
        if grp is None:
            raise RuntimeError("No group named %r." % args["group"])
        dp.ungroup(grp)
        return {"ungrouped": args["group"]}
    names = set(args["names"])
    # doc.createInstance("...ShapeCollection") returns None here — the collection
    # must come from the office service manager.
    state = _connect()
    coll = state["smgr"].createInstanceWithContext(
        "com.sun.star.drawing.ShapeCollection", state["ctx"])
    for i in range(dp.getCount()):
        shp = dp.getByIndex(i)
        if getattr(shp, "Name", None) in names:
            coll.add(shp)
    if coll.getCount() < 2:
        raise RuntimeError("Need >= 2 matching named shapes to group.")
    group = dp.group(coll)
    if args.get("group"):
        try:
            group.Name = args["group"]
        except Exception:
            pass
    return {"grouped": coll.getCount(), "name": getattr(group, "Name", "")}


def tool_calc_add_sparkline(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    target = sheet.getCellRangeByName(args["target_range"])
    try:
        groups = target.getSparklineGroups()
    except Exception:
        raise RuntimeError("Sparklines require LibreOffice 7.5+ "
                           "(getSparklineGroups is unavailable here).")
    src = sheet.getCellRangeByName(args["data_range"]).getRangeAddress()
    try:
        groups.addSparklines(src, target.getRangeAddress())
    except Exception as exc:
        raise RuntimeError("Could not add sparklines (%s). The Sparkline UNO API "
                           "varies by version." % type(exc).__name__)
    return {"sparkline": args["target_range"], "data": args["data_range"]}


# Calc error codes -> the marker the user actually sees in the cell.
_CALC_ERRORS = {
    501: "#NAME? (invalid character)", 502: "#VALUE! (invalid argument)",
    503: "#VALUE! (invalid floating point operation)",
    504: "#VALUE! (parameter list error)",
    508: "#NAME? (missing pair, e.g. a bracket)",
    509: "#NAME? (missing operator)", 510: "#NAME? (missing variable)",
    511: "#NAME? (missing variable)", 512: "#NAME? (formula too long)",
    513: "#NAME? (string too long)", 514: "#NUM! (internal overflow)",
    519: "#VALUE!", 520: "#NAME? (internal syntax error)",
    521: "#NAME? (internal syntax error)", 522: "#REF! (circular reference)",
    523: "#NUM! (calculation does not converge)",
    524: "#REF! (invalid reference — a row, column or sheet was deleted)",
    525: "#NAME? (unknown name)", 526: "#NAME?",
    527: "#REF! (nesting too deep)", 532: "#DIV/0! (division by zero)",
}


def tool_calc_detect_errors(args):
    """Find every broken formula in the workbook — the #REF!/#DIV/0!/#NAME?
    cells — with the formula that produced each one."""
    doc = _require_calc()
    sheets = doc.getSheets()
    if args.get("sheet") not in (None, ""):
        targets = [_resolve_sheet(doc, args["sheet"])]
    else:
        targets = [sheets.getByName(n) for n in sheets.getElementNames()]
    limit = int(args.get("max_results", 200))

    found = []
    for sheet in targets:
        if len(found) >= limit:
            break
        try:
            # FormulaResult.ERROR = 4 — one UNO call returns exactly the error
            # cells, instead of walking every cell of the used range.
            ranges = sheet.queryFormulaCells(4)
        except Exception:
            continue
        cells = ranges.getCells().createEnumeration()
        while cells.hasMoreElements() and len(found) < limit:
            cell = cells.nextElement()
            try:
                code = cell.getError()
            except Exception:
                code = 0
            addr = cell.getCellAddress()
            found.append({
                "sheet": sheet.getName(),
                "cell": "%s%d" % (_col_letters(addr.Column), addr.Row + 1),
                "error_code": code,
                "error": _CALC_ERRORS.get(code) or cell.getString() or "error %s" % code,
                "formula": cell.getFormula(),
            })
    return {"errors": found, "count": len(found),
            "truncated": len(found) >= limit,
            "sheets_scanned": [s.getName() for s in targets]}


TOOL_DEFS = [
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
    {"name": "calc_list_shapes",
     "description": "List everything on a sheet's DrawPage: shape names, types, positions/sizes (mm), text, OnClick script, and whether each is a form control. Use to verify buttons/shapes really exist where you think they do.",
     "inputSchema": _schema({"sheet": _SHEET})},
    {"name": "calc_delete_shape",
     "description": "Delete shape(s) with the given name from a sheet's DrawPage.",
     "inputSchema": _schema({"name": dict(_STR, description="shape name"), "sheet": _SHEET}, ["name"])},
    {"name": "calc_delete_chart",
     "description": "Remove an embedded chart from a sheet by name.",
     "inputSchema": _schema({"name": dict(_STR, description="chart name"), "sheet": _SHEET}, ["name"])},
    # --- calc P1/P2/P3 ---
    {"name": "calc_add_shape",
     "description": "Draw a rectangle/ellipse/line/text shape on a sheet at a position (position_cell or x_mm/y_mm) and size (mm), with optional fill/line color, caption text, and name.",
     "inputSchema": _schema({"sheet": _SHEET, "kind": dict(_STR, enum=["rectangle", "ellipse", "line", "text"]),
                             "position_cell": dict(_STR, description="anchor to this cell's top-left"),
                             "x_mm": _NUM, "y_mm": _NUM, "width_mm": _NUM, "height_mm": _NUM,
                             "fill_color": _STR, "line_color": _STR, "text": _STR, "name": _STR})},
    {"name": "calc_insert_image",
     "description": "Insert an image file onto a sheet at a position (position_cell or x_mm/y_mm) and optional size (mm; defaults to the image's native size).",
     "inputSchema": _schema({"path": _STR, "sheet": _SHEET, "position_cell": _STR,
                             "x_mm": _NUM, "y_mm": _NUM, "width_mm": _NUM, "height_mm": _NUM},
                            ["path"])},
    {"name": "calc_position_shape",
     "description": "Move (x_mm/y_mm), resize (width_mm/height_mm) or restack (z_order) an existing shape/image/chart on a sheet by name.",
     "inputSchema": _schema({"name": _STR, "sheet": _SHEET,
                             "x_mm": _NUM, "y_mm": _NUM, "width_mm": _NUM, "height_mm": _NUM,
                             "z_order": _INT}, ["name"])},
    {"name": "calc_edit_chart",
     "description": "Modify an existing chart: title, subtitle, legend on/off, x/y axis titles, and chart_type (column/bar/line/area/pie/...).",
     "inputSchema": _schema({"name": _STR, "sheet": _SHEET, "title": _STR, "subtitle": _STR,
                             "legend": _BOOL, "x_axis_title": _STR, "y_axis_title": _STR,
                             "chart_type": _STR}, ["name"])},
    {"name": "calc_list_charts",
     "description": "List embedded charts on a sheet with name, source ranges, and header flags.",
     "inputSchema": _schema({"sheet": _SHEET})},
    {"name": "calc_create_pivot",
     "description": "Create a pivot table (DataPilot) from a source range. 'fields' is a list of {field, orientation: row|column|page|data, function: sum|count|average|max|min}. Output anchored at output_cell.",
     "inputSchema": _schema({"name": _STR, "source_range": _RANGE, "output_cell": _STR,
                             "sheet": _SHEET,
                             "fields": {"type": "array", "items": {"type": "object"}}},
                            ["name", "source_range", "output_cell", "fields"])},
    {"name": "calc_refresh_pivot",
     "description": "Existing pivot tables on a sheet: action 'list', 'refresh' (one 'name' or all), or 'delete'.",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "refresh", "delete"]),
                             "name": _STR, "sheet": _SHEET})},
    {"name": "calc_add_subtotals",
     "description": "Apply grouped subtotals: group by column 'group_by' (0-based) and aggregate 'columns' (0-based list) with 'function' (sum/count/average/max/min); or remove=true to clear.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "group_by": _INT,
                             "columns": {"type": "array", "items": _INT},
                             "function": dict(_STR, enum=["sum", "count", "average", "max", "min"]),
                             "replace": _BOOL, "remove": _BOOL})},
    {"name": "calc_group_shapes",
     "description": "Group >=2 named shapes into one ('names' + optional 'group' name), or ungroup=true a group named 'group'.",
     "inputSchema": _schema({"sheet": _SHEET,
                             "names": {"type": "array", "items": _STR},
                             "group": _STR, "ungroup": _BOOL})},
    {"name": "calc_add_sparkline",
     "description": "Add in-cell sparklines driven by a data range (LibreOffice 7.5+).",
     "inputSchema": _schema({"target_range": _RANGE, "data_range": _RANGE, "sheet": _SHEET},
                            ["target_range", "data_range"])},
    {"name": "calc_statistics",
     "description": "Descriptive statistics over the NUMERIC cells in a Calc range: count, sum, mean, min, max, median, and population stdev. Text/empty cells ignored.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "calc_detect_errors",
     "description": "Find every broken formula in the workbook — #REF!, #DIV/0!, #NAME?, #VALUE!, circular references — reporting the sheet, cell, what the error means and the formula that caused it. Scans all sheets unless 'sheet' is given. Use this when a spreadsheet 'stopped working' or shows error markers.",
     "inputSchema": _schema({"sheet": _SHEET,
                             "max_results": dict(_INT, description="cap the list (default 200)")})},
]

register(globals(), TOOL_DEFS,
         basic=['calc_create_chart', 'calc_detect_errors'],
         read_only=['calc_detect_errors', 'calc_list_charts', 'calc_list_shapes', 'calc_statistics'])
