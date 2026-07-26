# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Calc tools — format."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




_H_ALIGN = {"left": "LEFT", "center": "CENTER", "right": "RIGHT",
            "justify": "BLOCK", "default": "STANDARD"}


# com.sun.star.util.NumberFormat is a CONSTANTS group, not an enum — the values
# are fixed by the API, so naming them here avoids a lookup per call.
_NUMBER_TYPES = {"number": 16, "currency": 8, "percent": 128, "date": 2,
                 "time": 4, "datetime": 6, "text": 256}


def _parse_locale(tag):
    """'ar-LY' / 'en_US' / 'de' -> a com.sun.star.lang.Locale. An empty tag gives
    the blank locale, which means 'whatever the document is set to'."""
    loc = _uno_struct("com.sun.star.lang.Locale")
    if tag:
        parts = str(tag).replace("_", "-").split("-")
        loc.Language = parts[0]
        if len(parts) > 1:
            loc.Country = parts[1].upper()
    return loc


def _number_format_key(doc, preset, locale_tag=None, decimals=None):
    """Resolve a named format in a LOCALE, so money/dates come out the way that
    locale writes them — ar-LY currency really is [$د.ل.‏-1001] #٬##0٫00, which
    no hand-written format string was ever going to get right."""
    if preset not in _NUMBER_TYPES:
        raise RuntimeError("number_preset must be one of %s"
                           % sorted(_NUMBER_TYPES))
    formats = doc.getNumberFormats()
    locale = _parse_locale(locale_tag)
    key = formats.getStandardFormat(_NUMBER_TYPES[preset], locale)
    if decimals is None:
        return key
    # ask the format service to restate the same format with N decimals
    try:
        base = formats.getByKey(key).FormatString
        wanted = formats.generateFormat(key, locale, False, False,
                                        int(decimals), 1)
        found = formats.queryKey(wanted, locale, False)
        return found if found != -1 else formats.addNew(wanted, locale)
    except Exception:
        return key


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
    if args.get("number_preset"):
        rng.NumberFormat = _number_format_key(
            doc, str(args["number_preset"]).lower(), args.get("locale"),
            args.get("decimals"))
        applied.append("number_preset")
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


def tool_calc_set_borders(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    width_pt = float(args.get("width_pt", 0.5))
    color = args.get("color", "#000000")
    rng.setPropertyValue("TableBorder2",
                         _full_grid_border(width_pt, color,
                                           bool(args.get("outline_only", False))))
    return {"range": args["range"], "width_pt": width_pt, "color": color,
            "outline_only": bool(args.get("outline_only", False))}


# --------------------------------------------------------------------------- #
# Tools — Calc conditional formatting & comments
# --------------------------------------------------------------------------- #

# Calc conditional-format operators -> com.sun.star.sheet.ConditionOperator names
_COND_OPERATORS = {
    "==": "EQUAL", "=": "EQUAL",
    "!=": "NOT_EQUAL", "<>": "NOT_EQUAL",
    ">": "GREATER", ">=": "GREATER_EQUAL",
    "<": "LESS", "<=": "LESS_EQUAL",
    "between": "BETWEEN", "not_between": "NOT_BETWEEN",
    "formula": "FORMULA",
}


def _ensure_cell_style(doc, name, fmt):
    """Create or update a Calc cell style with the given formatting (used as the
    'apply this when true' target of a conditional format)."""
    cell_styles = doc.getStyleFamilies().getByName("CellStyles")
    if cell_styles.hasByName(name):
        style = cell_styles.getByName(name)
    else:
        style = doc.createInstance("com.sun.star.style.CellStyle")
        cell_styles.insertByName(name, style)
    if "bold" in fmt:
        style.CharWeight = 150.0 if fmt["bold"] else 100.0
    if "italic" in fmt:
        style.CharPosture = _uno_enum("com.sun.star.awt.FontSlant",
                                      "ITALIC" if fmt["italic"] else "NONE")
    if "font_color" in fmt:
        style.CharColor = _hex_color(fmt["font_color"])
    if "background_color" in fmt:
        style.CellBackColor = _hex_color(fmt["background_color"])
    return name


def _cond_style_name(fmt):
    """A deterministic style name so identical formatting reuses one style and
    distinct formatting gets distinct styles."""
    parts = ["ClaudeCF"]
    if fmt.get("bold"):
        parts.append("b")
    if fmt.get("italic"):
        parts.append("i")
    if "background_color" in fmt:
        parts.append("bg" + str(fmt["background_color"]).lstrip("#"))
    if "font_color" in fmt:
        parts.append("fg" + str(fmt["font_color"]).lstrip("#"))
    return "_".join(parts)


def tool_calc_add_conditional_format(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])

    op_key = str(args.get("operator", ">")).lower()
    if op_key not in _COND_OPERATORS:
        raise RuntimeError("operator must be one of %s" % sorted(_COND_OPERATORS))

    fmt = {k: args[k] for k in ("bold", "italic", "font_color", "background_color")
           if k in args}
    if not fmt:
        raise RuntimeError("Give at least one format to apply when the condition "
                           "is true: background_color, font_color, bold, italic.")
    style_name = args.get("style_name") or _cond_style_name(fmt)
    _ensure_cell_style(doc, style_name, fmt)

    conditions = rng.getPropertyValue("ConditionalFormat")
    if args.get("replace_existing"):
        conditions.clear()
    op = _uno_enum("com.sun.star.sheet.ConditionOperator", _COND_OPERATORS[op_key])
    sep = _arg_separator(doc)     # a 'formula'-operator value may contain commas
    entry = (
        _pv("Operator", op),
        _pv("Formula1", _normalize_formula(
            str(args.get("value", args.get("formula1", ""))), sep)),
        _pv("Formula2", _normalize_formula(
            str(args.get("value2", args.get("formula2", ""))), sep)),
        _pv("StyleName", style_name),
    )
    conditions.addNew(entry)
    rng.setPropertyValue("ConditionalFormat", conditions)
    return {"range": args["range"], "operator": op_key, "style": style_name,
            "conditions": conditions.getCount()}


def tool_calc_clear_conditional_formats(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    conditions = rng.getPropertyValue("ConditionalFormat")
    removed = conditions.getCount()
    conditions.clear()
    rng.setPropertyValue("ConditionalFormat", conditions)
    return {"range": args["range"], "cleared": removed}


def _cell_addr_struct(sheet_index, col, row):
    addr = _uno_struct("com.sun.star.table.CellAddress")
    addr.Sheet = sheet_index
    addr.Column = col
    addr.Row = row
    return addr


def tool_calc_add_comment(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    cell = sheet.getCellRangeByName(args["cell"]).getRangeAddress()
    annotations = sheet.getAnnotations()
    # upsert: drop any existing comment on the same cell first
    for i in range(annotations.getCount() - 1, -1, -1):
        pos = annotations.getByIndex(i).getPosition()
        if pos.Column == cell.StartColumn and pos.Row == cell.StartRow:
            annotations.removeByIndex(i)
    annotations.insertNew(_cell_addr_struct(cell.Sheet, cell.StartColumn,
                                            cell.StartRow), args["text"])
    return {"cell": args["cell"], "comment": args["text"]}


def tool_calc_get_comments(args):
    doc = _require_calc()
    sheets = ([_resolve_sheet(doc, args["sheet"])]
              if args.get("sheet") not in (None, "")
              else [doc.getSheets().getByIndex(i)
                    for i in range(doc.getSheets().getCount())])
    out = []
    for sheet in sheets:
        annotations = sheet.getAnnotations()
        for i in range(annotations.getCount()):
            ann = annotations.getByIndex(i)
            pos = ann.getPosition()
            try:
                text = ann.getString()
            except Exception:
                text = ""
            out.append({"sheet": sheet.getName(),
                        "cell": "%s%d" % (_col_letters(pos.Column), pos.Row + 1),
                        "author": ann.getAuthor(), "text": text})
    return {"comments": out}


def tool_calc_set_validation(args):
    """Cell validity on a range: a dropdown 'list' (blocking by default) and/or
    an on-select 'hint' message; 'clear' removes validation."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    val = rng.Validation
    lst = args.get("list")
    if args.get("clear"):
        val.Type = _uno_enum("com.sun.star.sheet.ValidationType", "ANY")
        val.ShowInputMessage = False
        val.ShowErrorMessage = False
    if lst:
        val.Type = _uno_enum("com.sun.star.sheet.ValidationType", "LIST")
        val.ShowList = 1
        val.setFormula1(";".join('"%s"' % str(o) for o in lst))
        blocking = args.get("blocking", True)
        val.ShowErrorMessage = bool(blocking)
        if blocking:
            val.ErrorTitle = str(args.get("error_title") or "Invalid value")
            val.ErrorMessage = str(args.get("error_message")
                                   or "Choose one of: " + " / ".join(map(str, lst)))
    hint = args.get("hint")
    if hint:
        val.ShowInputMessage = True
        val.InputTitle = str(args.get("hint_title") or "")
        val.InputMessage = str(hint)
    rng.Validation = val
    return {"sheet": sheet.Name, "range": args["range"], "list": lst,
            "hint": hint, "cleared": bool(args.get("clear"))}


def tool_calc_delete_comment(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    cell = sheet.getCellRangeByName(args["cell"]).getRangeAddress()
    annotations = sheet.getAnnotations()
    removed = 0
    for i in range(annotations.getCount() - 1, -1, -1):
        pos = annotations.getByIndex(i).getPosition()
        if pos.Column == cell.StartColumn and pos.Row == cell.StartRow:
            annotations.removeByIndex(i)
            removed += 1
    return {"cell": args["cell"], "removed": removed}


def tool_calc_cell_protection(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    prot = _uno_struct("com.sun.star.util.CellProtection")
    prot.IsLocked = bool(args.get("locked", True))
    prot.IsFormulaHidden = bool(args.get("formula_hidden", False))
    prot.IsHidden = bool(args.get("hidden", False))
    prot.IsPrintHidden = bool(args.get("print_hidden", False))
    rng.CellProtection = prot
    return {"range": args["range"], "locked": prot.IsLocked,
            "note": "cell protection only takes effect once the sheet is protected"}


_VERT_JUSTIFY = {"standard": 0, "top": 1, "center": 2, "bottom": 3}


def tool_calc_format_cells_advanced(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    changed = []
    if args.get("vertical_align"):
        va = _VERT_JUSTIFY.get(str(args["vertical_align"]).lower())
        if va is None:
            raise RuntimeError("vertical_align must be one of %s." % sorted(_VERT_JUSTIFY))
        rng.VertJustify = va
        changed.append("vertical_align")
    if args.get("rotation") is not None:
        rng.RotateAngle = int(float(args["rotation"]) * 100)
        changed.append("rotation")
    if args.get("indent") is not None:
        rng.ParaIndent = _mm100(args["indent"])
        changed.append("indent")
    if args.get("shrink_to_fit") is not None:
        rng.ShrinkToFit = bool(args["shrink_to_fit"])
        changed.append("shrink_to_fit")
    if args.get("wrap") is not None:
        rng.IsTextWrapped = bool(args["wrap"])
        changed.append("wrap")
    return {"range": args["range"], "changed": changed}


def tool_calc_get_cell_format(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    cell = sheet.getCellRangeByName(args["cell"]).getCellByPosition(0, 0)
    out = {"cell": args["cell"]}
    try:
        out["number_format"] = doc.getNumberFormats().getByKey(
            cell.NumberFormat).FormatString
    except Exception:
        pass
    for key, prop in (("font", "CharFontName"), ("font_size", "CharHeight"),
                      ("weight", "CharWeight"), ("font_color", "CharColor"),
                      ("background_color", "CellBackColor"),
                      ("h_align", "HoriJustify"), ("cell_style", "CellStyle")):
        try:
            out[key] = _jsonable(cell.getPropertyValue(prop))
        except Exception:
            pass
    for ck in ("font_color", "background_color"):
        if isinstance(out.get(ck), int) and out[ck] >= 0:
            out[ck] = "#%06X" % out[ck]
    return out


def tool_calc_get_conditional_formats(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    out = []
    try:
        cfs = sheet.ConditionalFormats.getConditionalFormats()
    except Exception:
        cfs = []
    for cf in cfs:
        entry = {}
        try:
            # XConditionalFormat exposes the range as the `Range` PROPERTY
            # (there is no getRange() method on this build).
            entry["range"] = [_addr_to_a1(a)
                              for a in cf.Range.getRangeAddresses()]
        except Exception:
            pass
        conditions = []
        try:
            for i in range(cf.getCount()):
                c = cf.getByIndex(i)
                cond = {}
                for prop in ("Formula1", "Formula2", "StyleName"):
                    try:
                        cond[prop] = _jsonable(c.getPropertyValue(prop))
                    except Exception:
                        pass
                conditions.append(cond)
        except Exception:
            pass
        entry["conditions"] = conditions
        out.append(entry)
    return {"conditional_formats": out}


def tool_calc_get_validation(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    cell = sheet.getCellRangeByName(args["range"]).getCellByPosition(0, 0)
    val = cell.Validation
    out = {}
    for prop in ("Type", "ShowInputMessage", "InputTitle", "InputMessage",
                 "ShowErrorMessage", "ErrorTitle", "ErrorMessage", "ShowList"):
        try:
            out[prop] = _jsonable(val.getPropertyValue(prop))
        except Exception:
            pass
    try:
        out["Formula1"] = val.getFormula1()
        out["Formula2"] = val.getFormula2()
    except Exception:
        pass
    return {"validation": out}


def tool_calc_apply_cell_style(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    if args.get("style"):
        rng.CellStyle = args["style"]
        return {"applied_style": args["style"], "range": args["range"]}
    return {"cell_style": rng.getCellByPosition(0, 0).CellStyle,
            "range": args["range"]}


def tool_calc_add_scale_format(args):
    from com.sun.star.sheet.ConditionEntryType import COLORSCALE, DATABAR
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    addr = sheet.getCellRangeByName(args["range"]).getRangeAddress()
    ranges = doc.createInstance("com.sun.star.sheet.SheetCellRanges")
    ranges.addRangeAddress(addr, False)
    cfs = sheet.ConditionalFormats
    cf_id = cfs.createByRange(ranges)
    cf = None
    for c in cfs.getConditionalFormats():
        try:
            if c.ID == cf_id:
                cf = c
                break
        except Exception:
            pass
    if cf is None:
        raise RuntimeError("Could not create the conditional format entry.")
    kind = str(args.get("kind", "colorscale")).lower()
    try:
        cf.createEntry(DATABAR if kind == "databar" else COLORSCALE, 0)
    except Exception as exc:
        raise RuntimeError("Could not populate the %s entry (%s) — the scale-format "
                           "UNO API is version-sensitive." % (kind, type(exc).__name__))
    return {"scale_format": kind, "range": args["range"],
            "note": "created with default thresholds/colors; adjust in the UI if needed"}


# preset -> (header background, header font colour, body number format or None)
_TABLE_PRESETS = {
    "clean":     ("#EFEFEF", "#000000", None),
    "report":    ("#2F5597", "#FFFFFF", None),
    "financial": ("#2F5597", "#FFFFFF", "#,##0.00"),
}


def tool_calc_format_table(args):
    """One call for 'make this table look nice': header emphasis, a full border
    grid, auto-fitted columns and a frozen header row."""
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    preset = str(args.get("preset", "clean")).lower()
    if preset not in _TABLE_PRESETS:
        raise RuntimeError("preset must be one of %s" % sorted(_TABLE_PRESETS))
    head_bg, head_fg, number_format = _TABLE_PRESETS[preset]

    a1 = args.get("range")
    if not a1:
        addr = _sheet_used_addr(sheet)
        if _addr_is_empty(sheet, addr):
            raise RuntimeError("Sheet %r is empty — nothing to format."
                               % sheet.getName())
        a1 = _addr_to_a1(addr)
    rng = sheet.getCellRangeByName(a1)
    addr = rng.getRangeAddress()
    has_header = bool(args.get("header", True))
    applied = []

    rng.setPropertyValue("TableBorder2", _full_grid_border(0.5, "#B0B0B0", False))
    applied.append("borders")

    if has_header:
        header = sheet.getCellRangeByPosition(
            addr.StartColumn, addr.StartRow, addr.EndColumn, addr.StartRow)
        header.CharWeight = 150.0
        header.CellBackColor = _hex_color(head_bg)
        header.CharColor = _hex_color(head_fg)
        applied.append("header")

    body_start = addr.StartRow + (1 if has_header else 0)
    if number_format and body_start <= addr.EndRow:
        body = sheet.getCellRangeByPosition(
            addr.StartColumn, body_start, addr.EndColumn, addr.EndRow)
        formats = doc.getNumberFormats()
        locale = _uno_struct("com.sun.star.lang.Locale")
        key = formats.queryKey(number_format, locale, False)
        if key == -1:
            key = formats.addNew(number_format, locale)
        body.NumberFormat = key
        applied.append("number_format")

    cols = rng.getColumns()
    for i in range(cols.getCount()):
        cols.getByIndex(i).OptimalWidth = True
    applied.append("auto_fit_columns")

    if has_header and args.get("freeze", True):
        try:
            ctrl = doc.getCurrentController()
            ctrl.setActiveSheet(sheet)
            ctrl.freezeAtPosition(0, addr.StartRow + 1)
            applied.append("freeze")
        except Exception:
            pass  # headless / no view — formatting still landed

    # ponytail: no alternating row banding. Doing it per row is O(rows) UNO
    # calls; the cheap route is ONE conditional format keyed on MOD(ROW();2)
    # plus a named cell style — add that if "banded" is actually asked for.
    return {"range": a1, "sheet": sheet.getName(), "preset": preset,
            "applied": applied}


TOOL_DEFS = [
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
                             "number_format": dict(_STR, description="raw LibreOffice number format code, e.g. '#,##0.00'"),
                             "number_preset": dict(_STR, enum=["number", "currency", "percent", "date", "time", "datetime", "text"],
                                                   description="named format resolved FOR A LOCALE — prefer this over number_format for money and dates, it gets the currency symbol, digit grouping and date order right per country"),
                             "locale": dict(_STR, description="BCP-47 tag for number_preset, e.g. 'en-US', 'ar-LY', 'de-DE'; omit for the document's own locale"),
                             "decimals": dict(_INT, description="decimal places for number_preset"),
                             "auto_fit_columns": _BOOL}, ["range"])},
    {"name": "calc_merge_cells",
     "description": "Merge (merge=true, default) or unmerge (merge=false) a Calc range.",
     "inputSchema": _schema({"range": _RANGE, "merge": _BOOL, "sheet": _SHEET}, ["range"])},
    # --- calc conditional formatting & comments ---
    {"name": "calc_add_conditional_format",
     "description": "Add a conditional format to a range: when a cell meets the condition, a style with the given formatting is applied. Operators: '>', '>=', '<', '<=', '==', '!=', 'between' (value+value2), 'not_between', 'formula' (value is a formula that must be non-zero). Give at least one of background_color/font_color/bold/italic. Stacks with existing conditions unless replace_existing=true.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "operator": dict(_STR, enum=[">", ">=", "<", "<=", "==", "!=", "between", "not_between", "formula"]),
                             "value": dict(description="threshold / Formula1 (number, or a formula for operator 'formula')"),
                             "value2": dict(description="upper bound for 'between'/'not_between'"),
                             "background_color": dict(_STR, description="'#RRGGBB' applied when true"),
                             "font_color": dict(_STR, description="'#RRGGBB' applied when true"),
                             "bold": _BOOL, "italic": _BOOL,
                             "style_name": dict(_STR, description="reuse/name the applied cell style (optional)"),
                             "replace_existing": dict(_BOOL, description="clear existing conditions on the range first")},
                            ["range", "value"])},
    {"name": "calc_clear_conditional_formats",
     "description": "Remove all conditional formats from a Calc range.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "calc_add_comment",
     "description": "Add (or replace) a cell comment/annotation on a single cell.",
     "inputSchema": _schema({"cell": dict(_STR, description="a single cell, e.g. 'B2'"),
                             "text": _STR, "sheet": _SHEET}, ["cell", "text"])},
    {"name": "calc_get_comments",
     "description": "List cell comments on one sheet, or across all sheets if 'sheet' is omitted: [{sheet, cell, author, text}].",
     "inputSchema": _schema({"sheet": _SHEET})},
    {"name": "calc_set_borders",
     "description": "Draw borders around/through a Calc range (table styling). Full grid by default; outline_only=true draws only the outer border.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "width_pt": dict(_NUM, description="line width in points (default 0.5)"),
                             "color": dict(_STR, description="'#RRGGBB' (default black)"),
                             "outline_only": _BOOL}, ["range"])},
    {"name": "calc_set_validation",
     "description": "Cell validity for a range: 'list' shows a dropdown (blocking wrong entries unless blocking=false), 'hint' shows an on-select help message, 'clear' removes validation. List and hint can combine.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "list": {"type": "array", "items": _STR, "description": "dropdown entries"},
                             "blocking": dict(_BOOL, description="reject entries outside the list (default true)"),
                             "hint": dict(_STR, description="on-select help message"),
                             "hint_title": _STR, "error_title": _STR, "error_message": _STR,
                             "clear": dict(_BOOL, description="remove existing validation first")},
                            ["range"])},
    {"name": "calc_delete_comment",
     "description": "Delete the cell comment/annotation on a cell (companion to calc_add_comment / calc_get_comments).",
     "inputSchema": _schema({"cell": dict(_STR, description="e.g. 'B2'"), "sheet": _SHEET}, ["cell"])},
    {"name": "calc_cell_protection",
     "description": "Set locked/formula-hidden/hidden/print-hidden protection attributes on a range. Only takes effect once the sheet is protected (protect_document).",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "locked": _BOOL, "formula_hidden": _BOOL,
                             "hidden": _BOOL, "print_hidden": _BOOL}, ["range"])},
    {"name": "calc_format_cells_advanced",
     "description": "Advanced cell presentation: vertical_align (standard/top/center/bottom), rotation (degrees), indent (mm), shrink_to_fit, wrap.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "vertical_align": dict(_STR, enum=["standard", "top", "center", "bottom"]),
                             "rotation": _NUM, "indent": _NUM,
                             "shrink_to_fit": _BOOL, "wrap": _BOOL}, ["range"])},
    {"name": "calc_get_cell_format",
     "description": "Read a cell's number-format code, font, size, weight, colors (hex), horizontal alignment, and applied cell style.",
     "inputSchema": _schema({"cell": dict(_STR, description="e.g. 'B2'"), "sheet": _SHEET}, ["cell"])},
    {"name": "calc_get_conditional_formats",
     "description": "Read back the conditional formats on a sheet: their ranges and per-condition Formula1/Formula2/StyleName.",
     "inputSchema": _schema({"sheet": _SHEET})},
    {"name": "calc_get_validation",
     "description": "Read back the data-validation rule on a range (type, formulas, input/error messages, dropdown flag).",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "calc_apply_cell_style",
     "description": "Apply a named cell style (e.g. 'Good', 'Heading 1') to a range, or read the current style if 'style' is omitted.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "style": _STR}, ["range"])},
    {"name": "calc_add_scale_format",
     "description": "Add a color-scale or data-bar conditional format to a range (kind colorscale|databar), with default thresholds/colors.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "kind": dict(_STR, enum=["colorscale", "databar"])}, ["range"])},
    {"name": "calc_format_table",
     "description": "Make a data range look like a finished table in ONE call: bold coloured header, full border grid, auto-fitted columns and a frozen header row. Presets: clean (grey header), report (blue header), financial (blue header + #,##0.00 on the body). Defaults to the sheet's used range.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "preset": dict(_STR, enum=["clean", "report", "financial"]),
                             "header": dict(_BOOL, description="treat row 1 as a header (default true)"),
                             "freeze": dict(_BOOL, description="freeze the header row (default true)")})},
]

register(globals(), TOOL_DEFS,
         basic=['calc_format_range', 'calc_format_table'],
         read_only=['calc_get_cell_format', 'calc_get_comments', 'calc_get_conditional_formats', 'calc_get_validation'])
