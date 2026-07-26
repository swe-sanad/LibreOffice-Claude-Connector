# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Calc tools — data."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




# --------------------------------------------------------------------------- #
# Tools — Calc data
# --------------------------------------------------------------------------- #

def tool_calc_read_range(args):
    ub = _bridge()
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    return {"range": args["range"], "cells": ub.read_range_grid(rng)}


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
    sep = _arg_separator(doc)
    rng.setFormulaArray(tuple(
        tuple("" if v is None else _normalize_formula(str(v), sep) for v in row)
        for row in formulas))
    out = {"written": args["range"], "rows": rows, "columns": cols}
    if sep != ",":
        out["arg_separator"] = sep
    errors, incomplete = _range_errors(rng)
    if errors:
        out["errors"] = errors
    if incomplete:
        out["error_scan"] = "skipped (range too large to verify cell-by-cell)"
    return out


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
        desc.setPropertyValue("SearchRegularExpression", bool(args.get("regex", False)))
        total += sheet.replaceAll(desc)
    return {"replacements": total, "sheets_searched": len(sheets),
            "regex": bool(args.get("regex", False))}


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


def tool_calc_select_range(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    controller = doc.getCurrentController()
    controller.setActiveSheet(sheet)
    controller.select(sheet.getCellRangeByName(args["range"]))
    return {"selected": args["range"], "sheet": sheet.getName()}


def tool_read_spreadsheet(_args):
    """Read every sheet's used range at once: {sheet_name: 2-D values}. A whole
    workbook in one call, instead of one calc_read_range per sheet."""
    doc = _require_calc()
    sheets = doc.getSheets()
    out = {}
    for i in range(sheets.getCount()):
        sh = sheets.getByIndex(i)
        cur = sh.createCursor()
        cur.gotoEndOfUsedArea(False)
        a = cur.getRangeAddress()
        if a.EndColumn == 0 and a.EndRow == 0 and \
                sh.getCellByPosition(0, 0).getString() == "":
            out[sh.getName()] = []
            continue
        rng = sh.getCellRangeByPosition(0, 0, a.EndColumn, a.EndRow)
        out[sh.getName()] = [list(row) for row in rng.getDataArray()]
    return {"sheets": out, "count": len(out)}


def tool_calc_sort_range(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    keys = args.get("keys")
    if not keys:
        raise RuntimeError("Provide 'keys': a non-empty list of sort columns.")
    fields = []
    for k in keys:
        f = _uno_struct("com.sun.star.table.TableSortField")
        f.Field = int(k["column"])          # 0-based offset within the range
        f.IsAscending = not bool(k.get("descending", False))
        f.IsCaseSensitive = bool(k.get("case_sensitive", False))
        fields.append(f)
    desc = list(rng.createSortDescriptor())
    for pv in desc:
        if pv.Name == "SortFields":
            # MUST be a typed UNO sequence — a bare tuple is silently ignored
            # and rng.sort() then no-ops (reporting success but not sorting).
            pv.Value = _any_seq("com.sun.star.table.TableSortField", fields)
        elif pv.Name == "ContainsHeader":
            pv.Value = bool(args.get("has_header", False))
        elif pv.Name == "BindFormatsToContent":
            pv.Value = False
    rng.sort(tuple(desc))
    return {"sorted": args["range"], "keys": len(fields),
            "has_header": bool(args.get("has_header", False))}


def tool_calc_recalculate(args):
    doc = _require_calc()
    hard = bool(args.get("hard", True))
    if hard:
        doc.calculateAll()
    else:
        doc.calculate()
    return {"recalculated": "all" if hard else "dirty"}


def tool_calc_autofilter(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    dbr = doc.DatabaseRanges
    name = args.get("name") or ("Claude_AF_%s" % sheet.getName())
    enable = bool(args.get("enable", True))
    if dbr.hasByName(name):
        dbr.removeByName(name)
    if not enable:
        return {"autofilter": "off", "name": name}
    addr = sheet.getCellRangeByName(args["range"]).getRangeAddress()
    dbr.addNewByName(name, addr)
    dbr.getByName(name).AutoFilter = True
    return {"autofilter": "on", "range": args["range"], "name": name}


def tool_calc_goal_seek(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    fcell = sheet.getCellRangeByName(args["formula_cell"]).getCellByPosition(0, 0)
    vcell = sheet.getCellRangeByName(args["variable_cell"]).getCellByPosition(0, 0)
    res = doc.seekGoal(fcell.getCellAddress(), vcell.getCellAddress(),
                       str(args["target"]))
    applied = bool(args.get("apply", True))
    if applied:
        vcell.setValue(res.Result)
    return {"result": res.Result, "divergence": res.Divergence, "applied": applied}


def tool_calc_fill_series(args):
    from com.sun.star.sheet.FillDirection import TO_BOTTOM, TO_RIGHT, TO_TOP, TO_LEFT
    from com.sun.star.sheet.FillMode import LINEAR, GROWTH, DATE, AUTO
    from com.sun.star.sheet.FillDateMode import FILL_DATE_DAY
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    direction = {"down": TO_BOTTOM, "right": TO_RIGHT, "up": TO_TOP,
                 "left": TO_LEFT}.get(str(args.get("direction", "down")).lower(),
                                      TO_BOTTOM)
    mode = {"linear": LINEAR, "growth": GROWTH, "date": DATE,
            "auto": AUTO}.get(str(args.get("mode", "linear")).lower(), LINEAR)
    step = float(args.get("step", 1))
    end = float(args["end"]) if args.get("end") is not None else 1.7976931348623157e+308
    rng.fillSeries(direction, mode, FILL_DATE_DAY, step, end)
    return {"filled": args["range"], "mode": str(args.get("mode", "linear"))}


def tool_calc_standard_filter(args):
    from com.sun.star.sheet.FilterOperator import (EQUAL, NOT_EQUAL, GREATER,
                                                   GREATER_EQUAL, LESS, LESS_EQUAL)
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    desc = rng.createFilterDescriptor(True)
    op_map = {"=": EQUAL, "==": EQUAL, "!=": NOT_EQUAL, "<>": NOT_EQUAL,
              ">": GREATER, ">=": GREATER_EQUAL, "<": LESS, "<=": LESS_EQUAL}
    fields = []
    for cond in args["conditions"]:
        ff = _uno_struct("com.sun.star.sheet.TableFilterField")
        ff.Field = int(cond["column"])
        ff.Operator = op_map.get(str(cond.get("operator", "=")), EQUAL)
        v = cond["value"]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            ff.IsNumeric = True
            ff.NumericValue = float(v)
        else:
            ff.IsNumeric = False
            ff.StringValue = str(v)
        fields.append(ff)
    desc.setFilterFields(tuple(fields))
    try:
        desc.setPropertyValue("ContainsHeader", bool(args.get("has_header", True)))
    except Exception:
        pass
    rng.filter(desc)
    return {"filtered": args["range"], "conditions": len(fields)}


def tool_calc_multiple_operations(args):
    from com.sun.star.sheet.TableOperationMode import COLUMN, ROW, BOTH
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    target = sheet.getCellRangeByName(args["range"])
    formulas = sheet.getCellRangeByName(args["formula_range"]).getRangeAddress()
    mode = {"column": COLUMN, "row": ROW,
            "both": BOTH}.get(str(args.get("mode", "column")).lower(), COLUMN)

    def _cell_addr(a1):
        return sheet.getCellRangeByName(a1).getCellByPosition(0, 0).getCellAddress()

    col_in = _cell_addr(args["column_input"]) if args.get("column_input") else None
    row_in = _cell_addr(args["row_input"]) if args.get("row_input") else None
    if col_in is None:
        col_in = row_in
    if row_in is None:
        row_in = col_in
    if col_in is None:
        raise RuntimeError("Provide column_input and/or row_input.")
    # The formula cell(s) must sit OUTSIDE the filled range, else TABLE() is
    # written into the formula cell itself -> self-reference (Err:522).
    if _addr_intersects(target.getRangeAddress(), formulas):
        raise RuntimeError(
            "formula_range (%s) must be OUTSIDE range (%s): the formula sits in "
            "the row above (row mode) or column left of (column mode) the "
            "input+result block; 'range' covers only the inputs and result "
            "cells. Overlap makes every result a circular reference (Err:522)."
            % (args["formula_range"], args["range"]))
    target.setTableOperation(formulas, mode, col_in, row_in)
    out = {"table_operation": args["range"],
           "mode": str(args.get("mode", "column"))}
    errs, incomplete = _range_errors(target)
    if errs:
        out["errors"] = errs
    if incomplete:
        out["error_scan"] = "skipped (range too large to verify cell-by-cell)"
    return out


def tool_calc_remove_duplicates(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    data = [list(r) for r in rng.getDataArray()]
    if not data:
        return {"removed": 0, "kept": 0}
    header = bool(args.get("has_header", False))
    head = data[:1] if header else []
    body = data[1:] if header else data
    keys = args.get("key_columns")
    seen = set()
    survivors = []
    for row in body:
        k = tuple(row[i] for i in keys) if keys else tuple(row)
        if k in seen:
            continue
        seen.add(k)
        survivors.append(row)
    ncols = len(data[0])
    result = head + survivors
    while len(result) < len(data):
        result.append([""] * ncols)
    rng.setDataArray(tuple(tuple("" if v is None else v for v in r)
                           for r in result))
    return {"removed": len(body) - len(survivors), "kept": len(survivors)}


def tool_calc_transpose(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    src = sheet.getCellRangeByName(args["source_range"])
    data = [list(r) for r in src.getDataArray()]
    if not data:
        raise RuntimeError("Source range is empty.")
    trans = [list(col) for col in zip(*data)]
    tgt_sheet = (_resolve_sheet(doc, args["target_sheet"])
                 if args.get("target_sheet") else sheet)
    start = tgt_sheet.getCellRangeByName(args["target_cell"]).getRangeAddress()
    rows, cols = len(trans), len(trans[0])
    dest = tgt_sheet.getCellRangeByPosition(
        start.StartColumn, start.StartRow,
        start.StartColumn + cols - 1, start.StartRow + rows - 1)
    dest.setDataArray(tuple(tuple("" if v is None else v for v in r)
                            for r in trans))
    return {"transposed": "%dx%d -> %dx%d"
            % (len(data), len(data[0]), rows, cols)}


def tool_calc_export_range(args):
    """Export a cell range (or the used range) to a CSV or JSON file. format
    defaults to the path extension; CSV uses UTF-8-BOM + optional 'delimiter'."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng_name = args.get("range")
    if rng_name:
        rng = sheet.getCellRangeByName(rng_name)
    else:
        cur = sheet.createCursor()               # default: the sheet's used range
        cur.gotoStartOfUsedArea(False)
        cur.gotoEndOfUsedArea(True)
        rng = cur
    data = rng.getDataArray()
    grid = [list(row) for row in data]
    path = args["path"]
    fmt = (args.get("format")
           or os.path.splitext(path)[1].lstrip(".") or "csv").lower()
    if fmt == "json":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(grid, fh, ensure_ascii=False, indent=2)
    elif fmt == "csv":
        import csv
        delim = (args.get("delimiter") or ",")[0]
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh, delimiter=delim).writerows(grid)
    else:
        raise RuntimeError("format must be 'csv' or 'json', got %r." % fmt)
    return {"exported": path, "format": fmt, "rows": len(grid),
            "columns": len(grid[0]) if grid else 0}


def tool_calc_overview(args):
    """Cheap structural map of the whole workbook — bounded output whatever the
    file size, unlike read_spreadsheet which dumps every cell of every sheet."""
    ub = _bridge()
    doc = _require_calc()
    sample_rows = max(0, min(int(args.get("sample_rows", 3)), 20))
    col_cap = 20
    sheets = doc.getSheets()
    out = []
    for name in sheets.getElementNames():
        sheet = sheets.getByName(name)
        addr = _sheet_used_addr(sheet)
        if _addr_is_empty(sheet, addr):
            out.append({"sheet": name, "range": None, "rows": 0, "columns": 0})
            continue
        info = {"sheet": name,
                "range": _addr_to_a1(addr),
                "rows": addr.EndRow - addr.StartRow + 1,
                "columns": addr.EndColumn - addr.StartColumn + 1}
        if sample_rows:
            last_col = min(addr.EndColumn, addr.StartColumn + col_cap - 1)
            last_row = min(addr.EndRow, addr.StartRow + sample_rows - 1)
            grid = ub.read_range_grid(sheet.getCellRangeByPosition(
                addr.StartColumn, addr.StartRow, last_col, last_row))
            info["sample"] = grid
            info["sample_truncated_columns"] = addr.EndColumn > last_col
            # header heuristic: row 1 is all text, and some later row has a number
            if len(grid) >= 2:
                head = [c for c in grid[0] if c not in (None, "")]
                body = [c for row in grid[1:] for c in row]
                info["header_row_likely"] = bool(
                    head
                    and all(isinstance(c, str) for c in head)
                    and any(isinstance(c, (int, float))
                            and not isinstance(c, bool) for c in body))
        out.append(info)
    return {"sheets": out, "count": len(out),
            "active": doc.getCurrentController().getActiveSheet().getName()}


def tool_calc_clean_data(args):
    """Tidy a pasted/imported range: trim stray whitespace, turn numeric-looking
    text into real numbers, and drop fully empty rows. Formula cells are left
    untouched."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    a1 = args.get("range")
    if not a1:
        addr0 = _sheet_used_addr(sheet)
        if _addr_is_empty(sheet, addr0):
            return {"range": None, "trimmed": 0, "deleted_rows": 0,
                    "note": "sheet is empty"}
        a1 = _addr_to_a1(addr0)
    rng = sheet.getCellRangeByName(a1)
    addr = rng.getRangeAddress()

    # getFormulaArray gives formulas AND literals, so a rewrite preserves "=SUM(...)"
    grid = [list(row) for row in rng.getFormulaArray()]
    trimmed = 0
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            if isinstance(val, str) and val.startswith("="):
                continue  # a formula — never rewrite
            new = _clean_cell(val)
            if new != val:
                grid[r][c] = new
                trimmed += 1
    if trimmed:
        rng.setFormulaArray(tuple(tuple(row) for row in grid))

    deleted = 0
    if args.get("drop_empty_rows", True):
        rows = sheet.getRows()
        for r in range(len(grid) - 1, -1, -1):
            if all((v is None or v == "") for v in grid[r]):
                rows.removeByIndex(addr.StartRow + r, 1)
                deleted += 1

    return {"range": a1, "sheet": sheet.getName(), "trimmed_cells": trimmed,
            "deleted_empty_rows": deleted}


# --------------------------------------------------------------------------- #
# Tools — borrowed from the sibling projects, everyday/student slice only
#
# Mined from Nelson MCP's 140-tool surface (docs/COMPETITOR-STUDY.md). Only what
# a student or casual user reaches for. Deliberately NOT borrowed: ai_images_*,
# tunnel_*, launcher_*, gallery/docgallery, job/task/workflow, draw_* (a whole
# unsupported app), cross-document search indexes, and WriterAgent's
# data-science / symbolic-math / OCR / audio layer.
# --------------------------------------------------------------------------- #

def tool_calc_import_csv(args):
    """Import a CSV/TSV file INTO the open sheet — unlike open_document, which
    opens the file as its own separate spreadsheet."""
    import csv as _csv

    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("CSV not found: %s" % path)

    encoding = args.get("encoding") or "utf-8-sig"   # -sig strips an Excel BOM
    delimiter = args.get("delimiter")
    with open(path, "r", encoding=encoding, errors="replace", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        if not delimiter:
            try:
                delimiter = _csv.Sniffer().sniff(sample, ",;\t|").delimiter
            except Exception:
                delimiter = ","
        rows = list(_csv.reader(fh, delimiter=delimiter))
    if not rows:
        return {"imported": os.path.abspath(path), "rows": 0, "columns": 0}

    limit = int(args.get("max_rows", 0) or 0)
    truncated = bool(limit and len(rows) > limit)
    if truncated:
        rows = rows[:limit]
    width = max(len(r) for r in rows)

    # Values, never formulas. A CSV field starting with "=" is the classic CSV
    # injection vector, so this writes through setDataArray (which stores a
    # string as text) rather than setFormulaArray (which would evaluate it).
    grid = []
    for row in rows:
        padded = list(row) + [""] * (width - len(row))
        grid.append(tuple(float(v.strip()) if _looks_numeric(v.strip()) else v
                          for v in padded))

    origin = sheet.getCellRangeByName(args.get("start_cell") or "A1")
    addr = origin.getRangeAddress()
    target = sheet.getCellRangeByPosition(
        addr.StartColumn, addr.StartRow,
        addr.StartColumn + width - 1, addr.StartRow + len(rows) - 1)
    target.setDataArray(tuple(grid))

    return {"imported": os.path.abspath(path), "sheet": sheet.getName(),
            "range": _addr_to_a1(target.getRangeAddress()),
            "rows": len(rows), "columns": width, "delimiter": delimiter,
            "truncated": truncated}


def tool_calc_find(args):
    """Search a workbook WITHOUT replacing anything — the read-only counterpart
    to calc_find_replace, and the Calc twin of writer_find."""
    doc = _require_calc()
    sheets = doc.getSheets()
    if args.get("sheet") not in (None, ""):
        targets = [_resolve_sheet(doc, args["sheet"])]
    else:
        targets = [sheets.getByName(n) for n in sheets.getElementNames()]

    text = args.get("search")
    style = args.get("style")
    if not text and not style:
        raise RuntimeError("Give 'search' (text to look for) and/or 'style' "
                           "(a cell style name) to search by.")
    limit = int(args.get("max_results", 200))

    found = []
    for sheet in targets:
        if len(found) >= limit:
            break
        desc = sheet.createSearchDescriptor()
        # SearchStyles flips the meaning of SearchString from "this text" to
        # "cells using this style", so the two are mutually exclusive.
        desc.SearchString = style if style else text
        desc.SearchStyles = bool(style)
        desc.SearchCaseSensitive = bool(args.get("match_case", False))
        desc.SearchWords = bool(args.get("whole_words", False))
        desc.SearchRegularExpression = bool(args.get("regex", False)) and not style
        cells = sheet.findAll(desc)
        if cells is None:
            continue
        for i in range(cells.getCount()):
            if len(found) >= limit:
                break
            addr = cells.getByIndex(i).getRangeAddress()
            # a hit is a RANGE, not a cell: a style search over a styled A1:B1
            # comes back as one range, so reporting only its first cell would
            # silently under-report every match after the first column.
            for row in range(addr.StartRow, addr.EndRow + 1):
                for col in range(addr.StartColumn, addr.EndColumn + 1):
                    if len(found) >= limit:
                        break
                    cell = sheet.getCellByPosition(col, row)
                    found.append({
                        "sheet": sheet.getName(),
                        "cell": "%s%d" % (_col_letters(col), row + 1),
                        "value": cell.getString(),
                        "formula": cell.getFormula() or None,
                        "style": cell.CellStyle,
                    })
    return {"matches": found, "count": len(found),
            "truncated": len(found) >= limit,
            "searched": "style" if style else "text",
            "sheets_scanned": [s.getName() for s in targets]}


TOOL_DEFS = [
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
     "description": "Write a 2-D array of formula strings (or literals) into a Calc range; dimensions must match. Formulas may use ',' argument separators regardless of the document's locale (auto-normalized). The reply flags any resulting error cells in 'errors' (and 'error_scan' if the range was too large to verify).",
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
                             "regex": dict(_BOOL, description="treat 'search' as an ICU regular expression; $1..$n work in 'replace'"),
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
    {"name": "calc_select_range",
     "description": "Select a range in the LibreOffice window (activates the sheet and highlights the range for the user).",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "calc_sort_range",
     "description": "Sort a cell range by one or more key columns. 'keys' is a list of {column: 0-based offset within the range, descending?, case_sensitive?}. Set has_header to keep the first row in place.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "keys": {"type": "array", "items": {"type": "object"},
                                      "description": "e.g. [{\"column\":0},{\"column\":2,\"descending\":true}]"},
                             "has_header": dict(_BOOL, description="exclude a header row from the sort (default false)")},
                            ["range", "keys"])},
    {"name": "calc_recalculate",
     "description": "Force a recalculation after bulk formula writes: hard=true (default) recomputes everything, hard=false only dirty cells.",
     "inputSchema": _schema({"hard": dict(_BOOL, description="calculateAll (default true) vs calculate")})},
    {"name": "calc_find",
     "description": "Search a workbook WITHOUT changing anything — the read-only counterpart to calc_find_replace and the Calc twin of writer_find. Give 'search' for text (optionally 'regex'), or 'style' to list every cell using a named cell style (e.g. every cell styled 'Heading'), which is how you audit formatting. Returns sheet, cell, value, formula and style per hit. Searches every sheet unless 'sheet' is given.",
     "inputSchema": _schema({"search": dict(_STR, description="text to find"),
                             "style": dict(_STR, description="find cells using this CELL STYLE instead of text"),
                             "sheet": _SHEET,
                             "regex": dict(_BOOL, description="treat 'search' as a regular expression"),
                             "match_case": _BOOL,
                             "whole_words": _BOOL,
                             "max_results": dict(_INT, description="cap the list (default 200)")})},
    {"name": "calc_autofilter",
     "description": "Turn the AutoFilter dropdowns on for a range (enable=true, default) or off (enable=false).",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "enable": _BOOL,
                             "name": dict(_STR, description="database-range name (optional)")})},
    {"name": "calc_goal_seek",
     "description": "Solve for the variable-cell value that makes a formula cell reach 'target'; writes it back unless apply=false. Returns result + divergence.",
     "inputSchema": _schema({"formula_cell": _STR, "variable_cell": _STR, "target": _NUM,
                             "sheet": _SHEET, "apply": _BOOL},
                            ["formula_cell", "variable_cell", "target"])},
    {"name": "calc_fill_series",
     "description": "Fill a series across a range: direction (down/right/up/left), mode (linear/growth/date/auto), step, and optional end value.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "direction": dict(_STR, enum=["down", "right", "up", "left"]),
                             "mode": dict(_STR, enum=["linear", "growth", "date", "auto"]),
                             "step": _NUM, "end": _NUM}, ["range"])},
    {"name": "calc_standard_filter",
     "description": "Apply a criteria filter that hides non-matching rows. 'conditions' is a list of {column: 0-based, operator: =|!=|>|>=|<|<=, value}.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "has_header": _BOOL,
                             "conditions": {"type": "array", "items": {"type": "object"}}},
                            ["range", "conditions"])},
    {"name": "calc_multiple_operations",
     "description": "Build a what-if data table over a formula range against column and/or row input cells (mode column/row/both).",
     "inputSchema": _schema({"range": _RANGE, "formula_range": _STR, "sheet": _SHEET,
                             "mode": dict(_STR, enum=["column", "row", "both"]),
                             "column_input": _STR, "row_input": _STR},
                            ["range", "formula_range"])},
    {"name": "calc_remove_duplicates",
     "description": "Remove duplicate rows in a range (keep first). key_columns (0-based list) restricts the dedupe key; has_header keeps the first row.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "has_header": _BOOL,
                             "key_columns": {"type": "array", "items": _INT}}, ["range"])},
    {"name": "calc_transpose",
     "description": "Copy a range to a target cell with rows and columns swapped (optionally onto another sheet).",
     "inputSchema": _schema({"source_range": _RANGE, "target_cell": _STR,
                             "sheet": _SHEET, "target_sheet": _SHEET},
                            ["source_range", "target_cell"])},
    {"name": "calc_export_range",
     "description": "Export a cell 'range' (or the sheet's used range if omitted) to a CSV or JSON file at 'path'. format defaults to the path extension; CSV is UTF-8-BOM with an optional 'delimiter'.",
     "inputSchema": _schema({"path": _STR, "range": _RANGE, "sheet": _SHEET,
                             "format": dict(_STR, enum=["csv", "json"]),
                             "delimiter": dict(_STR, description="CSV delimiter (default ',')")},
                            ["path"])},
    {"name": "read_spreadsheet",
     "description": "Read every sheet's used range at once: {sheet_name: 2-D values} — a whole workbook in one call instead of one calc_read_range per sheet.",
     "inputSchema": _schema()},
    # --- everyday composites ---
    {"name": "calc_overview",
     "description": "Map the workbook cheaply before reading it: per sheet the used range, its row/column count, a few sample rows and whether row 1 looks like headers. Output stays small on a huge file — prefer this over read_spreadsheet to get your bearings.",
     "inputSchema": _schema({"sample_rows": dict(_INT, description="sample rows per sheet, 0-20 (default 3)")})},
    {"name": "calc_clean_data",
     "description": "Tidy a pasted or imported range: trim stray whitespace, turn numeric-looking text into real numbers, and drop fully empty rows. Formula cells are never rewritten. Defaults to the sheet's used range. NOTE: LibreOffice does not record bulk range writes for undo, so Ctrl+Z restores the deleted rows but not the trimmed values — say what will change before running it on data the user cannot re-import.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "drop_empty_rows": dict(_BOOL, description="default true")})},
    # --- everyday tools borrowed from the sibling projects ---
    {"name": "calc_import_csv",
     "description": "Import a CSV/TSV file INTO the open spreadsheet at a target cell — unlike open_document, which opens the file as its own separate document. Delimiter is auto-detected. Fields are written as text or numbers, never as formulas, so a field starting with '=' cannot execute.",
     "inputSchema": _schema({"path": dict(_STR, description="path to the .csv/.tsv file"),
                             "sheet": _SHEET,
                             "start_cell": dict(_STR, description="top-left target cell (default A1)"),
                             "delimiter": dict(_STR, description="force a delimiter; omit to auto-detect , ; tab |"),
                             "encoding": dict(_STR, description="file encoding (default utf-8, BOM tolerated)"),
                             "max_rows": dict(_INT, description="import at most this many rows (0 = all)")},
                            ["path"])},
]

register(globals(), TOOL_DEFS,
         basic=['calc_clean_data', 'calc_find', 'calc_get_used_range', 'calc_import_csv', 'calc_overview', 'calc_read_range', 'calc_set_formulas', 'calc_sort_range', 'calc_write_range'],
         read_only=['calc_export_range', 'calc_find', 'calc_get_formulas', 'calc_get_used_range', 'calc_overview', 'calc_read_range', 'calc_recalculate', 'calc_select_range', 'read_spreadsheet'])
