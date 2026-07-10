# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A minimal Model Context Protocol (MCP) server for LibreOffice.

This is the INVERSE of the .oxt extension: instead of embedding Claude inside
LibreOffice, this lets an external MCP client (Claude Code / Claude Desktop /
Cowork) reach IN and drive LibreOffice as a tool — read/write Calc ranges, read
Writer text, replace a selection, etc.

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
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL = "2024-11-05"

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "src")


def _log(message):
    sys.stderr.write("[libreoffice-mcp] %s\n" % message)
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# Lazy LibreOffice connection (reuses src/uno_bridge.py)
# --------------------------------------------------------------------------- #

_state = {"desktop": None}


def _bridge():
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    import uno_bridge  # noqa: E402 - lazy; needs the `uno` runtime
    return uno_bridge


def _desktop():
    if _state["desktop"] is None:
        ub = _bridge()
        port = int(os.environ.get("LO_UNO_PORT", "2002"))
        _log("connecting to LibreOffice on port %d ..." % port)
        _ctx, _smgr, desktop = ub.connect(port=port, retries=8, delay=0.5)
        _state["desktop"] = desktop
    return _state["desktop"]


def _current_doc():
    desktop = _desktop()
    doc = desktop.getCurrentComponent()
    if doc is None:
        # Headless / unfocused sessions have no "current" component even when
        # documents are open; fall back to the open-components list.
        docs = []
        enum = desktop.getComponents().createEnumeration()
        while enum.hasMoreElements():
            docs.append(enum.nextElement())
        if len(docs) == 1:
            return docs[0]
        if len(docs) > 1:
            raise RuntimeError(
                "%d documents are open but none is focused; focus the one to "
                "act on (or close the others)." % len(docs))
        raise RuntimeError("No document is currently open/active in LibreOffice.")
    return doc


def _resolve_sheet(doc, sheet):
    sheets = doc.getSheets()
    if sheet is None or sheet == "":
        return doc.getCurrentController().getActiveSheet()
    if isinstance(sheet, int):
        return sheets.getByIndex(sheet)
    return sheets.getByName(str(sheet))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

def tool_lo_status(_args):
    ub = _bridge()
    desktop = _desktop()
    docs = []
    enum = desktop.getComponents().createEnumeration()
    while enum.hasMoreElements():
        comp = enum.nextElement()
        try:
            title = comp.getTitle()
        except Exception:
            title = comp.getURL() if hasattr(comp, "getURL") else "?"
        kind = ("calc" if ub.is_calc(comp)
                else "writer" if ub.is_writer(comp) else "other")
        docs.append({"title": title, "type": kind})
    return {"connected": True, "documents": docs}


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


def tool_calc_read_range(args):
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_calc(doc):
        raise RuntimeError("The active document is not a Calc spreadsheet.")
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    return {"range": args["range"], "cells": ub.read_range_grid(rng)}


def tool_calc_write_range(args):
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_calc(doc):
        raise RuntimeError("The active document is not a Calc spreadsheet.")
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    cells = args["cells"]
    # dimensions must match the range (setDataArray requirement)
    addr = rng.getRangeAddress()
    rows = addr.EndRow - addr.StartRow + 1
    cols = addr.EndColumn - addr.StartColumn + 1
    if len(cells) != rows or any(len(r) != cols for r in cells):
        raise RuntimeError(
            "cells shape %dx%d does not match range %s (%dx%d)."
            % (len(cells), len(cells[0]) if cells else 0, args["range"], rows, cols))
    ub.write_range_grid(rng, cells)
    return {"written": args["range"], "rows": rows, "columns": cols}


def tool_writer_get_text(_args):
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_writer(doc):
        raise RuntimeError("The active document is not a Writer document.")
    return {"text": doc.getText().getString()}


def tool_writer_replace_selection(args):
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_writer(doc):
        raise RuntimeError("The active document is not a Writer document.")
    text = args["text"]
    _t, has_selection = ub.get_writer_selection(doc)
    if has_selection:
        ub.replace_writer_selection(doc, text)
        return {"action": "replaced"}
    ub.insert_writer_at_caret(doc, text)
    return {"action": "inserted_at_caret"}


TOOLS = {
    "lo_status": tool_lo_status,
    "list_documents": tool_list_documents,
    "get_current_selection": tool_get_current_selection,
    "calc_read_range": tool_calc_read_range,
    "calc_write_range": tool_calc_write_range,
    "writer_get_text": tool_writer_get_text,
    "writer_replace_selection": tool_writer_replace_selection,
}

_STR = {"type": "string"}
TOOL_DEFS = [
    {"name": "lo_status", "description": "Check the LibreOffice connection and list open documents.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_documents", "description": "List the documents currently open in LibreOffice.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_current_selection",
     "description": "Get the user's current selection: a Calc cell range (with data) or the selected Writer text.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "calc_read_range",
     "description": "Read a Calc cell range as a 2-D array. Operates on the active document.",
     "inputSchema": {"type": "object",
                     "properties": {"range": dict(_STR, description="A1 notation, e.g. 'A1:C10'"),
                                    "sheet": {"description": "sheet name or 0-based index; omit for the active sheet"}},
                     "required": ["range"]}},
    {"name": "calc_write_range",
     "description": "Write a 2-D array of values into a Calc range (dimensions must match the range).",
     "inputSchema": {"type": "object",
                     "properties": {"range": dict(_STR, description="A1 notation, e.g. 'A1:C10'"),
                                    "cells": {"type": "array", "items": {"type": "array"},
                                              "description": "rows of cell values (strings or numbers)"},
                                    "sheet": {"description": "sheet name or 0-based index; omit for the active sheet"}},
                     "required": ["range", "cells"]}},
    {"name": "writer_get_text", "description": "Get the full body text of the active Writer document.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "writer_replace_selection",
     "description": "Replace the current Writer selection with text (or insert at the caret if nothing is selected).",
     "inputSchema": {"type": "object",
                     "properties": {"text": dict(_STR, description="the replacement/insertion text")},
                     "required": ["text"]}},
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
    _log("LibreOffice MCP server ready (stdio). LO_UNO_PORT=%s"
         % os.environ.get("LO_UNO_PORT", "2002"))
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
