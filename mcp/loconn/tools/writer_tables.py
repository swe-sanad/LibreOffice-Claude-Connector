# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Writer tools — tables."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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

    # Position: after a matched paragraph ('search'), after a body-paragraph
    # index ('after_index'), or (default) at the document end.
    text = doc.getText()
    if args.get("search") or args.get("after_index") is not None:
        from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
        if args.get("search"):
            rng = _writer_find_first(doc, args["search"],
                                     args.get("match_case", False))
            if rng is None:
                raise RuntimeError("Search text %r not found." % args["search"])
            # The match may live in a header/footer/table cell — anchor to ITS
            # text object, not the body (body text here throws "End of content
            # node doesn't have the proper start node").
            anchor_text = rng.getText()
            cursor = anchor_text.createTextCursorByRange(rng.getEnd())
            cursor.gotoEndOfParagraph(False)
        else:
            idx = int(args["after_index"])
            paras = [p for _, p in _writer_paragraphs(doc)]
            if idx < 0 or idx >= len(paras):
                raise RuntimeError("No body paragraph at index %d (document "
                                   "has %d)." % (idx, len(paras)))
            anchor_text = text
            cursor = anchor_text.createTextCursorByRange(paras[idx].getEnd())
        anchor_text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
        anchor_text.insertTextContent(cursor, table, False)
    else:
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


def tool_writer_format_table(args):
    doc = _require_writer()
    tables = doc.getTextTables()
    name = args.get("name")
    if name:
        if not tables.hasByName(name):
            raise RuntimeError("No table named %r." % name)
        table = tables.getByName(name)
    else:
        idx = int(args.get("index", 0))
        if idx >= tables.getCount():
            raise RuntimeError("Table index %d out of range (%d tables)."
                               % (idx, tables.getCount()))
        table = tables.getByIndex(idx)
    applied = []

    if "border_width_pt" in args or "border_color" in args:
        table.setPropertyValue(
            "TableBorder2",
            _full_grid_border(float(args.get("border_width_pt", 0.5)),
                              args.get("border_color", "#000000")))
        applied.append("border")

    header = (bool(args.get("header_bold")) or "header_background" in args
              or "header_font_color" in args)
    if header:
        ncols = len(table.getColumns())
        for c in range(ncols):
            cell = table.getCellByPosition(c, 0)
            if "header_background" in args:
                cell.BackColor = _hex_color(args["header_background"])
                cell.BackTransparent = False
            cur = cell.getText().createTextCursor()
            cur.gotoEnd(True)
            if args.get("header_bold"):
                cur.CharWeight = 150.0
            if "header_font_color" in args:
                cur.CharColor = _hex_color(args["header_font_color"])
        applied.append("header_row")

    if not applied:
        raise RuntimeError("Give border_width_pt/border_color and/or "
                           "header_bold/header_background/header_font_color.")
    return {"table": table.getName(), "applied": applied}


def tool_writer_read_table(args):
    doc = _require_writer()
    tables = doc.getTextTables()
    name = args.get("name")
    if name not in (None, ""):
        if not tables.hasByName(name):
            raise RuntimeError("No table named %r. Tables: %s"
                               % (name, ", ".join(tables.getElementNames())))
        table = tables.getByName(name)
    else:
        if tables.getCount() == 0:
            raise RuntimeError("The document has no tables.")
        table = tables.getByIndex(int(args.get("index", 0)))
    rows = table.getRows().getCount()
    cols = table.getColumns().getCount()
    grid = []
    for r in range(rows):
        row = []
        for c in range(cols):
            cell = table.getCellByName("%s%d" % (_col_letters(c), r + 1))
            row.append(cell.getString() if cell is not None else None)
        grid.append(row)
    return {"name": table.Name, "rows": rows, "columns": cols, "cells": grid}


def tool_writer_edit_table(args):
    doc = _require_writer()
    tables = doc.getTextTables()
    name = args.get("name")
    if name not in (None, ""):
        if not tables.hasByName(name):
            raise RuntimeError("No table named %r. Tables: %s"
                               % (name, ", ".join(tables.getElementNames())))
        table = tables.getByName(name)
    else:
        if tables.getCount() == 0:
            raise RuntimeError("The document has no tables.")
        table = tables.getByIndex(int(args.get("index", 0)))
    actions = []
    if args.get("insert_rows"):
        table.getRows().insertByIndex(int(args.get("at_row", 0)),
                                      int(args["insert_rows"]))
        actions.append("insert_rows")
    if args.get("delete_rows"):
        table.getRows().removeByIndex(int(args.get("at_row", 0)),
                                      int(args["delete_rows"]))
        actions.append("delete_rows")
    if args.get("insert_columns"):
        table.getColumns().insertByIndex(int(args.get("at_column", 0)),
                                         int(args["insert_columns"]))
        actions.append("insert_columns")
    if args.get("delete_columns"):
        table.getColumns().removeByIndex(int(args.get("at_column", 0)),
                                         int(args["delete_columns"]))
        actions.append("delete_columns")
    if args.get("merge"):
        start, _, end = str(args["merge"]).partition(":")
        cur = table.createCursorByCellName(start)
        cur.gotoCellByName(end or start, True)
        cur.mergeRange()
        actions.append("merge")
    if args.get("cell") and args.get("background_color") is not None:
        table.getCellByName(args["cell"]).BackColor = _hex_color(
            args["background_color"])
        actions.append("background")
    if args.get("cell") and args.get("text") is not None:
        table.getCellByName(args["cell"]).setString(str(args["text"]))
        actions.append("cell_text")
    return {"table": table.Name, "actions": actions}


def tool_writer_sort_table(args):
    """Sort a Writer table's data rows by one key column. Reads the grid, sorts
    in Python (numeric-aware), writes cell text back."""
    doc = _require_writer()
    table = _resolve_table(doc, args)
    nrows = table.getRows().getCount()
    ncols = table.getColumns().getCount()
    key = int(args.get("key_column", 0))
    if key < 0 or key >= ncols:
        raise RuntimeError("key_column %d out of range (0..%d)." % (key, ncols - 1))
    has_header = bool(args.get("has_header", True))
    descending = bool(args.get("descending", False))
    grid = [[table.getCellByPosition(c, r).getString() for c in range(ncols)]
            for r in range(nrows)]
    head = grid[:1] if has_header else []
    body = grid[1:] if has_header else grid

    def _k(row):
        v = row[key] if key < len(row) else ""
        try:
            return (0, float(str(v).replace(",", "").strip()))  # numbers first
        except (ValueError, TypeError):
            return (1, str(v).lower())

    body.sort(key=_k, reverse=descending)
    # ponytail: sorts by cell text and writes text back; a numeric cell keeps its
    # digits as text (number-recognition is a display concern, not stored value).
    ordered = head + body
    for r in range(nrows):
        for c in range(ncols):
            table.getCellByPosition(c, r).setString(ordered[r][c])
    return {"table": table.Name, "rows_sorted": len(body),
            "key_column": key, "descending": descending}


def tool_writer_convert_table(args):
    """Convert a Writer table to delimited text ('to_text'), or a range of body
    paragraphs to a table ('to_table')."""
    doc = _require_writer()
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    direction = str(args.get("direction", "to_text")).lower()
    sep = args.get("separator")
    if sep is None or sep == "":
        sep = "\t"
    text = doc.getText()
    if direction == "to_text":
        table = _resolve_table(doc, args)
        nr, nc = table.getRows().getCount(), table.getColumns().getCount()
        grid = [[table.getCellByPosition(c, r).getString() for c in range(nc)]
                for r in range(nr)]
        els = []
        en = text.createEnumeration()
        while en.hasMoreElements():
            els.append(en.nextElement())
        ti = next((i for i, e in enumerate(els)
                   if e.supportsService("com.sun.star.text.TextTable")
                   and getattr(e, "Name", None) == table.Name), None)
        if ti is not None and ti + 1 < len(els):
            ins = text.createTextCursorByRange(els[ti + 1].getStart())
        else:
            ins = text.createTextCursorByRange(text.getEnd())
        for row in grid:
            text.insertString(ins, sep.join(row), False)
            text.insertControlCharacter(ins, PARAGRAPH_BREAK, False)
        text.removeTextContent(table)
        return {"direction": "to_text", "rows": nr, "columns": nc}
    if direction == "to_table":
        start = int(args["start"])
        count = int(args.get("count", 1))
        if count < 1:
            raise RuntimeError("count must be >= 1.")
        paras = [p for _, p in _writer_paragraphs(doc)]
        n = len(paras)
        if start < 0 or start >= n:
            raise RuntimeError("No body paragraph at index %d (document has %d)."
                               % (start, n))
        end = min(start + count, n)
        rows = [paras[i].getString().split(sep) for i in range(start, end)]
        ncols = max((len(r) for r in rows), default=1)
        rows = [r + [""] * (ncols - len(r)) for r in rows]
        table = doc.createInstance("com.sun.star.text.TextTable")
        table.initialize(len(rows), ncols)
        text.insertTextContent(
            text.createTextCursorByRange(paras[start].getStart()), table, False)
        for r in range(len(rows)):
            for c in range(ncols):
                table.getCellByPosition(c, r).setString(rows[r][c])
        # The table is not a paragraph, so the source paragraphs keep their
        # indices — delete them with the same range logic as delete_paragraphs.
        paras = [p for _, p in _writer_paragraphs(doc)]
        n = len(paras)
        if end < n:
            left, right = paras[start].getStart(), paras[end].getStart()
        else:
            left, right = paras[start - 1].getEnd(), paras[n - 1].getEnd()
        cur = text.createTextCursorByRange(left)
        cur.gotoRange(right, True)
        cur.setString("")
        return {"direction": "to_table", "table": table.Name,
                "rows": len(rows), "columns": ncols}
    raise RuntimeError("direction must be 'to_text' or 'to_table'.")


def tool_writer_table_formula(args):
    """Set a formula in a Writer table cell (e.g. '=<A1>+<A2>' or 'sum <A1:A3>')
    and return the computed value."""
    doc = _require_writer()
    table = _resolve_table(doc, args)
    cellname = args["cell"]
    formula = str(args["formula"]).lstrip("=")
    cell = table.getCellByName(cellname)
    if cell is None:
        raise RuntimeError("No cell %r in table %r." % (cellname, table.Name))
    cell.setFormula(formula)
    return {"table": table.Name, "cell": cellname,
            "formula": cell.getFormula(), "value": cell.getValue(),
            "text": cell.getString()}


def tool_writer_split_cells(args):
    """Split a table cell (or 'A1:B1' range) into N cells along columns or rows."""
    doc = _require_writer()
    table = _resolve_table(doc, args)
    into = int(args.get("into", 2))
    if into < 2:
        raise RuntimeError("into must be >= 2.")
    direction = str(args.get("direction", "columns")).lower()
    if direction not in ("columns", "rows"):
        raise RuntimeError("direction must be 'columns' or 'rows'.")
    horizontal = direction == "rows"     # bHorizontal True -> stacked rows
    cellspec = str(args["cell"])
    start, _, end = cellspec.partition(":")
    cur = table.createCursorByCellName(start)
    if end:
        cur.gotoCellByName(end, True)
    cur.splitRange(into - 1, horizontal)
    return {"table": table.Name, "cell": cellspec, "into": into,
            "direction": direction}


def tool_writer_repeat_heading_rows(args):
    """Make a table's first N rows repeat as a header on every page it spans
    (or turn that off with repeat=false). Target by 'name' or 0-based 'index'."""
    doc = _require_writer()
    table = _resolve_table(doc, args)
    repeat = bool(args.get("repeat", True))
    table.RepeatHeadline = repeat
    if repeat:
        table.HeaderRowCount = int(args.get("rows", 1))
    return {"table": table.Name, "repeat": repeat,
            "header_rows": table.HeaderRowCount}


def tool_writer_list_tables(_args):
    """List every table with 0-based index, name, dimensions, and a header-row
    preview — the discovery companion to writer_edit_table/sort/convert."""
    doc = _require_writer()
    tables = doc.getTextTables()
    out = []
    for i in range(tables.getCount()):
        t = tables.getByIndex(i)
        nr, nc = t.getRows().getCount(), t.getColumns().getCount()
        try:
            header = [t.getCellByPosition(c, 0).getString() for c in range(min(nc, 8))]
        except Exception:
            header = []
        out.append({"index": i, "name": t.Name, "rows": nr,
                    "columns": nc, "header": header})
    return {"tables": out, "count": len(out)}


TOOL_DEFS = [
    {"name": "writer_insert_table",
     "description": "Insert a table, optionally filled with data (rows of strings/numbers). By default appends at the document end; give 'search' to place it right after the first paragraph containing that text, or 'after_index' to place it after a 0-based body-paragraph index.",
     "inputSchema": _schema({"rows": _INT, "columns": _INT, "data": _GRID,
                             "search": dict(_STR, description="place the table after the paragraph containing this text"),
                             "after_index": dict(_INT, description="place the table after this 0-based body-paragraph index"),
                             "match_case": _BOOL}, ["rows", "columns"])},
    {"name": "writer_format_table",
     "description": "Format a Writer table (by name or 0-based index): draw a full-grid border (width in pt + color) and/or style the header row (bold, background color, font color).",
     "inputSchema": _schema({"name": dict(_STR, description="table name; or use index"),
                             "index": dict(_INT, description="0-based table index (default 0)"),
                             "border_width_pt": _NUM,
                             "border_color": dict(_STR, description="'#RRGGBB'"),
                             "header_bold": _BOOL,
                             "header_background": dict(_STR, description="'#RRGGBB'"),
                             "header_font_color": dict(_STR, description="'#RRGGBB'")})},
    {"name": "writer_read_table",
     "description": "Read an existing Writer table back as a 2-D grid of cell strings. Give 'name' (from writer_list_objects / find) or a 0-based 'index' (default 0).",
     "inputSchema": _schema({"name": dict(_STR, description="table name (e.g. 'Table1')"),
                             "index": dict(_INT, description="0-based table index if no name")})},
    {"name": "writer_edit_table",
     "description": "Edit an existing Writer table (by 'name' or 0-based 'index'): insert/delete rows/columns (at_row/at_column), merge a cell range ('A1:B2'), and set a cell's background color and/or text ('cell' + 'background_color'/'text') — editing a cell after insert.",
     "inputSchema": _schema({"name": _STR, "index": _INT,
                             "insert_rows": _INT, "delete_rows": _INT, "at_row": _INT,
                             "insert_columns": _INT, "delete_columns": _INT, "at_column": _INT,
                             "merge": dict(_STR, description="cell range to merge, e.g. 'A1:B2'"),
                             "cell": dict(_STR, description="cell for background/text, e.g. 'A1'"),
                             "background_color": _STR,
                             "text": dict(_STR, description="replace the 'cell' text")})},
    # --- menu coverage: Table / Format / Style / Form / Tools ---
    {"name": "writer_sort_table",
     "description": "Sort a Writer table's data rows by one key column (0-based 'key_column'), ascending or 'descending'. 'has_header' (default true) keeps row 0 pinned. Numeric-aware. Target by 'name' or 0-based 'index'.",
     "inputSchema": _schema({"name": _STR, "index": _INT,
                             "key_column": dict(_INT, description="0-based column to sort on (default 0)"),
                             "descending": _BOOL, "has_header": _BOOL})},
    {"name": "writer_convert_table",
     "description": "Convert between a table and text. direction 'to_text': turn a table (by 'name' or 0-based 'index') into rows of paragraphs, cells joined by 'separator' (default tab). direction 'to_table': turn body paragraphs [start, start+count) into a table, splitting each on 'separator' (default tab) into columns.",
     "inputSchema": _schema({"direction": dict(_STR, enum=["to_text", "to_table"]),
                             "name": _STR, "index": _INT,
                             "start": dict(_INT, description="to_table: first paragraph index (0-based)"),
                             "count": dict(_INT, description="to_table: how many paragraphs (default 1)"),
                             "separator": dict(_STR, description="cell delimiter (default tab)")},
                            ["direction"])},
    {"name": "writer_table_formula",
     "description": "Set a formula in a Writer table cell and return the computed value. Writer cell-reference syntax, e.g. '=<A1>+<A2>', '=<A1>*2', 'sum <A1:A5>'. Target the table by 'name' or 0-based 'index'.",
     "inputSchema": _schema({"cell": dict(_STR, description="cell name, e.g. 'A3'"),
                             "formula": dict(_STR, description="e.g. '=<A1>+<A2>'"),
                             "name": _STR, "index": _INT},
                            ["cell", "formula"])},
    {"name": "writer_split_cells",
     "description": "Split a table cell (or an 'A1:B1' range) into 'into' cells (default 2) along 'columns' (default) or 'rows'. Target the table by 'name' or 0-based 'index'.",
     "inputSchema": _schema({"cell": dict(_STR, description="cell 'A1' or range 'A1:B1'"),
                             "into": dict(_INT, description="number of cells to split into (default 2)"),
                             "direction": dict(_STR, enum=["columns", "rows"]),
                             "name": _STR, "index": _INT},
                            ["cell"])},
    {"name": "writer_repeat_heading_rows",
     "description": "Make a table's first 'rows' (default 1) repeat as a header on every page the table spans, or turn it off with repeat=false. Target the table by 'name' or 0-based 'index'.",
     "inputSchema": _schema({"name": _STR, "index": _INT,
                             "rows": dict(_INT, description="how many header rows (default 1)"),
                             "repeat": dict(_BOOL, description="on (default) or off")})},
    {"name": "writer_list_tables",
     "description": "List every table with 0-based index, name, row/column counts, and a header-row preview — discovery for writer_edit_table / writer_sort_table / writer_convert_table / writer_table_formula.",
     "inputSchema": _schema()},
]

register(globals(), TOOL_DEFS,
         basic=['writer_insert_table'],
         read_only=['writer_list_tables', 'writer_read_table'])
