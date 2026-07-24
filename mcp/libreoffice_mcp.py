# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
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
SERVER_VERSION = "0.9.1"
DEFAULT_PROTOCOL = "2024-11-05"

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "src")


def _log(message):
    sys.stderr.write("[libreoffice-mcp] %s\n" % message)
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# Lazy LibreOffice connection (reuses src/uno_bridge.py)
# --------------------------------------------------------------------------- #

_state = {"ctx": None, "smgr": None, "desktop": None, "transport": None,
          "arg_sep": None}


def _bridge():
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    import uno_bridge  # noqa: E402 - lazy; needs the `uno` runtime
    return uno_bridge


def _find_soffice():
    """Locate the soffice executable: LO_SOFFICE env var, next to the running
    interpreter (the bundled python lives in LibreOffice/program), then the
    standard install locations per platform."""
    cand = os.environ.get("LO_SOFFICE")
    if cand and os.path.exists(cand):
        return cand
    exedir = os.path.dirname(sys.executable)
    guesses = [os.path.join(exedir, "soffice.exe"),
               os.path.join(exedir, "soffice"),
               r"C:\Program Files\LibreOffice\program\soffice.exe",
               r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
               "/usr/bin/soffice", "/usr/local/bin/soffice",
               "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    for g in guesses:
        if os.path.exists(g):
            return g
    return None


def _autostart_office(port):
    """Zero-setup path: if no LibreOffice is listening, launch one with the UNO
    socket ourselves. Disable with LO_AUTOSTART=0; LO_HEADLESS=1 for headless.
    Caveat: if a LibreOffice instance is ALREADY running without a listener,
    the new launch is swallowed by it (single-instance) and the accept arg is
    ignored — the retry then fails with a clear message."""
    if os.environ.get("LO_AUTOSTART", "1").strip().lower() in ("0", "false", "no"):
        return False
    exe = _find_soffice()
    if not exe:
        return False
    import subprocess
    args = [exe, "--norestore", "--nologo",
            "--accept=socket,host=localhost,port=%d;urp;" % port]
    if os.environ.get("LO_HEADLESS", "").strip().lower() in ("1", "true", "yes"):
        args.insert(1, "--headless")
    kwargs = {"close_fds": True}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — outlive this server
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    _log("auto-starting LibreOffice: %s" % " ".join(args))
    subprocess.Popen(args, **kwargs)
    return True


def _default_pipe_name():
    # MUST stay identical to default_pipe_name() in src/agent_acceptor.py —
    # the extension opens the pipe, this side dials it.
    import getpass
    import re as _re
    user = _re.sub(r"[^a-z0-9-]", "-", getpass.getuser().lower()) or "user"
    return "lo-claude-" + user


def _connect():
    if _state["desktop"] is None:
        ub = _bridge()
        port = int(os.environ.get("LO_UNO_PORT", "2002"))
        # LO_UNO_PIPE and CLAUDE_AGENT_PIPE are both honored (the extension
        # reads the latter); LO_UNO_PIPE=0/off skips the pipe rung entirely.
        pipe = (os.environ.get("LO_UNO_PIPE")
                or os.environ.get("CLAUDE_AGENT_PIPE")
                or _default_pipe_name())

        # 1) the agent-acceptor extension's named pipe: reaches a LibreOffice
        #    the user opened normally (no flags). One quick try — it's local.
        if pipe.strip().lower() not in ("0", "off", "false", "no"):
            try:
                ctx, smgr, desktop = ub.connect_pipe(pipe, retries=1, delay=0.2)
                _state.update(ctx=ctx, smgr=smgr, desktop=desktop, transport="pipe")
                _log("connected over pipe %r" % pipe)
                return _state
            except Exception:
                pass

        # 2) the classic socket; 3) auto-launch with the socket accept arg.
        _log("connecting to LibreOffice on port %d ..." % port)
        try:
            ctx, smgr, desktop = ub.connect(port=port, retries=3, delay=0.5)
        except Exception as exc:
            if not _is_connection_error(exc) or not _autostart_office(port):
                raise
            _log("no listener on port %d — launched LibreOffice, waiting ..." % port)
            try:
                ctx, smgr, desktop = ub.connect(port=port, retries=30, delay=1.0)
            except Exception:
                raise RuntimeError(
                    "Launched LibreOffice but still no UNO listener on port %d. "
                    "Most likely another LibreOffice instance was already running "
                    "WITHOUT a listener (single-instance swallows the new launch). "
                    "Close all LibreOffice windows and retry — or install the "
                    "agent-acceptor extension (claude-connector .oxt) so every "
                    "running LibreOffice is reachable, or start it yourself: "
                    'soffice --norestore "--accept=socket,host=localhost,port=%d;urp;"'
                    % (port, port))
        _state.update(ctx=ctx, smgr=smgr, desktop=desktop, transport="socket")
    return _state


def _reset_connection():
    """Drop the cached UNO connection so the next call reconnects fresh."""
    _state.update(ctx=None, smgr=None, desktop=None, transport=None, arg_sep=None)


# Substrings (lower-cased) that mark a lost/disposed UNO bridge — i.e. the office
# was restarted since we cached the connection. Kept tight so a normal tool error
# that merely mentions one of these words doesn't trigger a spurious reconnect.
_CONN_ERROR_MARKERS = (
    "urp bridge",          # "Binary URP bridge already disposed / disposed during call"
    "disposedexception",   # com.sun.star.lang.DisposedException
    "noconnectexception",  # office not up yet while we reconnect
    "connection refused", "wsaeconnrefused",
    "broken pipe", "connection closed", "connection was aborted",
)


def _is_connection_error(exc):
    """True when `exc` looks like a lost/disposed UNO bridge, not a tool bug."""
    blob = (type(exc).__name__ + " " + str(exc)).lower()
    return any(marker in blob for marker in _CONN_ERROR_MARKERS)


def _call_with_reconnect(func, args):
    """Run a tool; if the UNO bridge was lost since we cached the connection
    (LibreOffice restarted), drop the stale connection and retry ONCE. This is
    what makes the server survive an office restart instead of returning
    'Binary URP bridge already disposed' forever."""
    try:
        return func(args)
    except Exception as exc:
        if not _is_connection_error(exc):
            raise
        _log("UNO bridge lost (%s) - reconnecting and retrying once" % exc)
        _reset_connection()
        return func(args)


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


def _select_doc(args):
    """Pick a SPECIFIC open document by 'index' (0-based), 'url' (substring), or
    'title' (substring). Returns None when no selector is given (caller decides
    the default). Raises — listing the open docs — when a selector matches
    nothing. Shared by set_active_document and close_document so the caller can
    target a doc explicitly instead of relying on unreliable GUI focus."""
    if (args.get("index") is None and not args.get("url")
            and not args.get("title")):
        return None
    docs = _open_docs()
    if not docs:
        raise RuntimeError("No documents are open.")
    target = None
    if args.get("index") is not None:
        i = int(args["index"])
        if i < 0 or i >= len(docs):
            raise RuntimeError("index %d out of range (0..%d)." % (i, len(docs) - 1))
        target = docs[i]
    elif args.get("url"):
        want = str(args["url"]).replace("\\", "/").lower()
        target = next((d for d in docs
                       if want in ((d.getURL() or "").replace("\\", "/").lower())),
                      None)
    else:  # title
        want = str(args["title"]).lower()
        for d in docs:
            try:
                tt = d.getTitle()
            except Exception:
                tt = ""
            if want in (tt or "").lower():
                target = d
                break
    if target is None:
        listing = "; ".join("%d:%s" % (i, _doc_info(d)["title"])
                            for i, d in enumerate(docs))
        raise RuntimeError("No open document matched. Open: %s" % listing)
    return target


def _activate(doc):
    """Bring a document's window to the front so getCurrentComponent() and the
    focus-based tools target it. Best-effort — never fatal."""
    try:
        doc.getCurrentController().getFrame().activate()
    except Exception:
        pass


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
    """Resolve by 0-based index (int, float or numeric string), exact name, or
    the English token of a bilingual 'english | عربي' tab name. Raises with the
    actual sheet list instead of a blank UNO NoSuchElementException."""
    sheets = doc.getSheets()
    if sheet is None or sheet == "":
        return doc.getCurrentController().getActiveSheet()
    if isinstance(sheet, (int, float)) and not isinstance(sheet, bool):
        return sheets.getByIndex(int(sheet))
    name = str(sheet).strip()
    if name.isdigit():
        return sheets.getByIndex(int(name))
    if sheets.hasByName(name):
        return sheets.getByName(name)
    want = name.split("|")[0].strip().lower()
    for nm in sheets.getElementNames():
        if nm.lower() == name.lower() or nm.split("|")[0].strip().lower() == want:
            return sheets.getByName(nm)
    raise RuntimeError("No sheet matches %r. Sheets: %s"
                       % (name, " ; ".join(sheets.getElementNames())))


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


def _any_seq(type_name, items):
    """Wrap a Python sequence of UNO structs/values as a TYPED UNO sequence Any.

    Assigning a bare Python tuple where UNO wants a `[]com.sun.star...` sequence
    is silently marshalled as the wrong type — the call then no-ops or throws
    IllegalArgumentException. Everywhere a sequence-of-struct is handed to a UNO
    API (setPropertyValue / replaceByName / replaceByIndex / sort descriptors),
    route it through here. See the FilterData / chapter-numbering call sites."""
    import uno
    return uno.Any("[]" + type_name, tuple(items))


def _hex_color(value):
    """'#RRGGBB' (or 'RRGGBB') -> int, as UNO colors are plain ints."""
    s = str(value).lstrip("#")
    if len(s) != 6:
        raise RuntimeError("Colors must be '#RRGGBB', got: %r" % value)
    return int(s, 16)


def _mm100(mm):
    """Millimetres -> 1/100 mm (the unit for most UNO layout properties)."""
    return int(round(float(mm) * 100))


def _pt_to_mm100(pt):
    """Points -> 1/100 mm (for border/line widths)."""
    return int(round(float(pt) * 2540.0 / 72.0))


def _border_line(width_pt, color):
    line = _uno_struct("com.sun.star.table.BorderLine2")
    line.LineWidth = _pt_to_mm100(width_pt)
    line.Color = _hex_color(color) if color is not None else 0
    line.LineStyle = 0   # com.sun.star.table.BorderLineStyle.SOLID
    return line


def _full_grid_border(width_pt, color, outline_only=False):
    tb = _uno_struct("com.sun.star.table.TableBorder2")
    line = _border_line(width_pt, color)
    tb.TopLine = line;    tb.IsTopLineValid = True
    tb.BottomLine = line; tb.IsBottomLineValid = True
    tb.LeftLine = line;   tb.IsLeftLineValid = True
    tb.RightLine = line;  tb.IsRightLineValid = True
    inner = _border_line(0 if outline_only else width_pt, color)
    tb.HorizontalLine = inner; tb.IsHorizontalLineValid = True
    tb.VerticalLine = inner;   tb.IsVerticalLineValid = True
    return tb


# Paragraph alignment names -> com.sun.star.style.ParagraphAdjust
_PARA_ADJUST = {"left": "LEFT", "right": "RIGHT", "center": "CENTER",
                "justify": "BLOCK", "block": "BLOCK"}

# Common paper sizes in 1/100 mm (portrait width, height)
_PAPER = {"a4": (21000, 29700), "a5": (14800, 21000), "a3": (29700, 42000),
          "letter": (21590, 27940), "legal": (21590, 35560)}


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


def _addr_intersects(a, b):
    """True when two CellRangeAddress rectangles overlap (same sheet)."""
    return (a.Sheet == b.Sheet
            and a.StartColumn <= b.EndColumn and a.EndColumn >= b.StartColumn
            and a.StartRow <= b.EndRow and a.EndRow >= b.StartRow)


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


# --------------------------------------------------------------------------- #
# Tools — status & selection
# --------------------------------------------------------------------------- #

def tool_lo_status(_args):
    _connect()
    out = {"connected": True,
           "transport": _state.get("transport"),
           "documents": [_doc_info(doc) for doc in _open_docs()]}
    try:      # WHICH office answered (crucial when a pipe reaches a stray one)
        ps = _state["smgr"].createInstanceWithContext(
            "com.sun.star.util.PathSettings", _state["ctx"])
        out["profile"] = ps.UserConfig
    except Exception:
        pass
    return out


def tool_list_documents(_args):
    return tool_lo_status(_args)


def tool_lo_screenshot(args):
    """Capture the LibreOffice WINDOW itself via PrintWindow — the only
    reliable way to see what the GUI actually renders (PDF export can lie:
    e.g. form controls on RTL sheets render in print but not on screen, or
    vice versa). Captures even when the window is behind others. Windows-only.
    """
    import sys as _sys
    if not _sys.platform.startswith("win"):
        raise RuntimeError("lo_screenshot is currently Windows-only.")
    import ctypes
    import ctypes.wintypes as wt
    import os
    import struct
    import tempfile
    import zlib

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:                                   # physical pixels from GetWindowRect
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    kernel32 = ctypes.windll.kernel32

    def _proc_name(hwnd):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(h)

    want = str(args.get("window_title") or "").lower()
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
                if want and want not in title.lower():
                    return True
                # only real LibreOffice windows — a browser tab titled
                # "LibreOffice - Google Chrome" must never match
                if _proc_name(hwnd) in ("soffice.bin", "soffice.exe"):
                    hits.append((hwnd, title))
        return True

    user32.EnumWindows(_enum, 0)
    if not hits:
        raise RuntimeError("No visible LibreOffice window%s found. "
                           "Is LibreOffice running with a GUI (not --headless)?"
                           % ((" with title containing %r" % args["window_title"])
                              if args.get("window_title") else ""))
    hwnd, title = hits[0]

    if user32.IsIconic(hwnd):                      # minimized -> restore first
        import time as _time
        user32.ShowWindow(hwnd, 9)                 # SW_RESTORE
        _time.sleep(1.0)

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 100 or h <= 100:
        raise RuntimeError("Window %r has no usable area (%dx%d) — restore or "
                           "resize the LibreOffice window." % (title, w, h))

    wdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(wdc)
    bmp = gdi32.CreateCompatibleBitmap(wdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        user32.PrintWindow(hwnd, mdc, 2)          # 2 = PW_RENDERFULLCONTENT

        class _BIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        bih = _BIH()
        bih.biSize = ctypes.sizeof(_BIH)
        bih.biWidth = w
        bih.biHeight = -h                          # top-down
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0                      # BI_RGB
        raw = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mdc, bmp, 0, h, raw, ctypes.byref(bih), 0)
        if got != h:
            raise RuntimeError("GetDIBits returned %d of %d rows." % (got, h))
        data = raw.raw
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, wdc)

    # BGRA -> minimal RGB PNG (bundled python has no PIL; zlib is enough)
    stride = w * 4
    rows = []
    for y in range(h):
        bgra = data[y * stride:(y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3] = bgra[2::4]
        rgb[1::3] = bgra[1::4]
        rgb[2::3] = bgra[0::4]
        rows.append(b"\x00" + bytes(rgb))          # filter type 0 per scanline

    def _chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
           + _chunk(b"IEND", b""))

    path = args.get("path") or os.path.join(tempfile.gettempdir(),
                                            "lo_screenshot.png")
    path = os.path.abspath(path)
    with open(path, "wb") as f:
        f.write(png)
    return {"saved": path, "width": w, "height": h, "window": title}


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
    _activate(doc)   # make the new doc the active one for subsequent calls
    return {"created": _doc_info(doc)}


def tool_open_document(args):
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("File not found: %s" % path)
    doc = _desktop().loadComponentFromURL(_to_url(path), "_blank", 0, ())
    if doc is None:
        raise RuntimeError("LibreOffice could not open: %s" % path)
    _activate(doc)
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
    # Prefer an explicit target (index/title/url); fall back to the active doc.
    # Focus-based resolution alone once closed the WRONG document, so callers
    # can — and for safety should — name which doc to close.
    doc = _select_doc(args) or _current_doc()
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


def _arg_separator(doc):
    """The document's ACTUAL function-argument separator (',' or ';'), detected
    at runtime and cached. Localized builds (Arabic, most of Europe) use ';'
    because their decimal separator is ',' — so a comma-separated formula like
    =SUM(1,2,3) silently computes to #NAME?/Err. Probed on a throwaway temp
    sheet so user data is never touched."""
    sep = _state.get("arg_sep")
    if sep:
        return sep
    sep = ","
    try:
        sheets = doc.getSheets()
        probe = "__lo_mcp_sep_probe__"
        if sheets.hasByName(probe):
            # Name already taken (real sheet / stale crashed run) — never touch
            # or delete it; fall back to the safe default.
            _state["arg_sep"] = sep
            return sep
        was_modified = doc.isModified()
        sheets.insertNewByName(probe, sheets.getCount())
        try:
            cell = sheets.getByName(probe).getCellByPosition(0, 0)
            cell.setFormula("=SUM(1,2)")
            if cell.getError() != 0:      # comma rejected -> ';' locale
                sep = ";"
        finally:
            # Cleanup must NOT clobber a successful detection, and a probe should
            # not leave the document dirty.
            try:
                sheets.removeByName(probe)
            except Exception:
                pass
            try:
                doc.setModified(was_modified)
            except Exception:
                pass
    except Exception:
        sep = ","                         # conservative on any probe failure
    _state["arg_sep"] = sep
    return sep


def _normalize_formula(s, sep):
    """Rewrite TOP-LEVEL ',' argument separators to `sep`, skipping commas inside
    "..." string literals AND {...} array constants (whose separators follow a
    different locale convention). No-op when the doc already uses ','."""
    if sep == "," or "," not in s:
        return s
    out, in_str, brace = [], False, 0
    for ch in s:
        if ch == '"':
            in_str = not in_str
            out.append(ch)
        elif in_str:
            out.append(ch)
        elif ch == "{":
            brace += 1
            out.append(ch)
        elif ch == "}":
            brace = max(0, brace - 1)
            out.append(ch)
        elif ch == "," and brace == 0:
            out.append(sep)
        else:
            out.append(ch)
    return "".join(out)


def _range_errors(rng, max_cells=4096):
    """Cells in a range holding an error value (Err:5xx / #NAME? / #REF! ...),
    as ([{cell, code, text}], incomplete). Each cell is a cross-process read, so
    ranges above `max_cells` are NOT scanned (incomplete=True) rather than stall a
    large write; a scan that itself fails partway also reports incomplete — so a
    partial/skipped scan is never mistaken for 'no errors'."""
    errs, incomplete = [], False
    try:
        addr = rng.getRangeAddress()
        rows = addr.EndRow - addr.StartRow + 1
        cols = addr.EndColumn - addr.StartColumn + 1
        if rows * cols > max_cells:
            return errs, True
        for r in range(rows):
            for c in range(cols):
                cell = rng.getCellByPosition(c, r)
                code = cell.getError()
                if code:
                    errs.append({"cell": "%s%d" % (_col_letters(addr.StartColumn + c),
                                                   addr.StartRow + r + 1),
                                 "code": int(code), "text": cell.getString()})
    except Exception:
        incomplete = True
    return errs, incomplete


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
# Tools — Calc conditional formatting & comments
# --------------------------------------------------------------------------- #

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
    idx = 0
    while enum.hasMoreElements():
        para = enum.nextElement()
        try:
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
            level = int(para.getPropertyValue("OutlineLevel"))
        except Exception:
            continue
        # 'idx' is the body-paragraph index — matches writer_get_paragraphs and
        # the start/index params of writer_format_paragraph / _apply_style / etc.
        if level > 0:
            outline.append({"level": level, "text": para.getString(),
                            "index": idx,
                            "style": para.getPropertyValue("ParaStyleName")})
        idx += 1
    return {"outline": outline}


_ANNOTATION = "com.sun.star.text.TextField.Annotation"


def tool_writer_add_comment(args):
    ub = _bridge()
    doc = _require_writer()
    field = doc.createInstance(_ANNOTATION)
    field.Author = args.get("author", "Claude")
    field.Content = args["text"]

    if args.get("search"):
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findFirst(desc)
        if found is None:
            raise RuntimeError("search text not found: %r" % args["search"])
        found.getText().insertTextContent(found, field, True)
        return {"action": "comment_added", "anchored_to": args["search"]}

    # else: anchor to the current selection, or at the end of the document
    _t, has_selection = ub.get_writer_selection(doc)
    if has_selection:
        cursor = doc.getCurrentController().getViewCursor()
        cursor.getText().insertTextContent(cursor, field, True)
        return {"action": "comment_added", "anchored_to": "selection"}
    text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, field, False)
    return {"action": "comment_added", "anchored_to": "document_end"}


def tool_writer_get_comments(_args):
    doc = _require_writer()
    out = []
    enum = doc.getTextFields().createEnumeration()
    while enum.hasMoreElements():
        field = enum.nextElement()
        if not field.supportsService(_ANNOTATION):
            continue
        entry = {"author": field.Author, "text": field.Content}
        try:
            entry["anchor"] = field.getAnchor().getString()
        except Exception:
            pass
        try:
            entry["resolved"] = bool(field.getPropertyValue("Resolved"))
        except Exception:
            pass
        out.append(entry)
    return {"comments": out}


def tool_writer_add_conditional_section(args):
    """Writer has no cell-style conditional formatting; its genuine analog is a
    CONDITIONAL SECTION — a named block of text hidden/shown by a formula. The
    section is hidden when `condition` evaluates TRUE (LibreOffice semantics)."""
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    doc = _require_writer()
    name = args["name"]
    if doc.getTextSections().hasByName(name):
        raise RuntimeError("A section named %r already exists." % name)

    text = doc.getText()
    end = text.createTextCursorByRange(text.getEnd())
    if text.getString() != "":
        text.insertControlCharacter(end, PARAGRAPH_BREAK, False)
        end.collapseToEnd()
    anchor_start = text.createTextCursorByRange(end.getStart())
    text.insertString(end, args.get("text", ""), False)

    span = text.createTextCursorByRange(anchor_start)
    span.gotoEndOfParagraph(True)

    section = doc.createInstance("com.sun.star.text.TextSection")
    section.setName(name)
    text.insertTextContent(span, section, True)

    # Set Condition / IsVisible AFTER insertion — properties set on a
    # not-yet-inserted section are dropped (so visible=false didn't hide it).
    applied = doc.getTextSections().getByName(name)
    applied.Condition = args["condition"]
    if "visible" in args:
        applied.IsVisible = bool(args["visible"])
    return {"section": name, "condition": args["condition"],
            "is_visible": bool(applied.IsVisible),
            "currently_visible": bool(applied.IsCurrentlyVisible)}


# --------------------------------------------------------------------------- #
# Tools — Writer paragraph / page / table styling
# --------------------------------------------------------------------------- #

def _apply_para_format(target, args):
    applied = []
    if "align" in args:
        key = str(args["align"]).lower()
        if key not in _PARA_ADJUST:
            raise RuntimeError("align must be one of %s" % sorted(_PARA_ADJUST))
        target.ParaAdjust = _uno_enum("com.sun.star.style.ParagraphAdjust",
                                      _PARA_ADJUST[key])
        applied.append("align")
    if "line_spacing_percent" in args:
        spacing = _uno_struct("com.sun.star.style.LineSpacing")
        spacing.Mode = 0   # com.sun.star.style.LineSpacingMode.PROP
        spacing.Height = int(args["line_spacing_percent"])
        target.ParaLineSpacing = spacing
        applied.append("line_spacing_percent")
    if "space_above_mm" in args:
        target.ParaTopMargin = _mm100(args["space_above_mm"])
        applied.append("space_above_mm")
    if "space_below_mm" in args:
        target.ParaBottomMargin = _mm100(args["space_below_mm"])
        applied.append("space_below_mm")
    if "indent_left_mm" in args:
        target.ParaLeftMargin = _mm100(args["indent_left_mm"])
        applied.append("indent_left_mm")
    if "indent_right_mm" in args:
        target.ParaRightMargin = _mm100(args["indent_right_mm"])
        applied.append("indent_right_mm")
    if "first_line_indent_mm" in args:
        target.ParaFirstLineIndent = _mm100(args["first_line_indent_mm"])
        applied.append("first_line_indent_mm")
    if "style_name" in args:
        target.ParaStyleName = args["style_name"]
        applied.append("style_name")
    return applied


def tool_writer_format_paragraph(args):
    doc = _require_writer()
    if not any(k in args for k in ("align", "line_spacing_percent",
                                   "space_above_mm", "space_below_mm",
                                   "indent_left_mm", "indent_right_mm",
                                   "first_line_indent_mm", "style_name")):
        raise RuntimeError("Give at least one paragraph property: align, "
                           "line_spacing_percent, space_above_mm, space_below_mm, "
                           "indent_left_mm, indent_right_mm, first_line_indent_mm, "
                           "style_name.")
    # index-range targeting (0-based, pairs with writer_get_paragraphs);
    # takes precedence over search when 'start'/'count' are given.
    if "start" in args or "count" in args:
        start = int(args.get("start", 0))
        cnt = args.get("count")
        applied = []
        n = 0
        for i, para in _writer_paragraphs(doc):
            if i < start:
                continue
            if cnt is not None and i >= start + int(cnt):
                break
            applied = _apply_para_format(para, args)
            n += 1
        return {"paragraphs_formatted": n, "applied": applied}
    if args.get("search"):
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findAll(desc)
        count = found.getCount()
        applied = []
        for i in range(count):
            applied = _apply_para_format(found.getByIndex(i), args)
        return {"paragraphs_formatted": count, "applied": applied}
    # no search: every body paragraph
    count = 0
    applied = []
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        para = enum.nextElement()
        if para.supportsService("com.sun.star.text.Paragraph"):
            applied = _apply_para_format(para, args)
            count += 1
    return {"paragraphs_formatted": count, "applied": applied}


def _page_style(doc, name=None):
    styles = doc.getStyleFamilies().getByName("PageStyles")
    if name:
        if not styles.hasByName(name):
            raise RuntimeError("No page style named %r." % name)
        return styles.getByName(name)
    # the page style actually in use by the first paragraph, else 'Standard'
    try:
        cursor = doc.getText().createEnumeration().nextElement()
        used = cursor.getPropertyValue("PageStyleName")
        if used and styles.hasByName(used):
            return styles.getByName(used)
    except Exception:
        pass
    return styles.getByName("Standard")


def tool_writer_set_page_style(args):
    doc = _require_writer()
    style = _page_style(doc, args.get("style_name"))
    applied = []

    width = height = None
    if "paper" in args:
        key = str(args["paper"]).lower()
        if key not in _PAPER:
            raise RuntimeError("paper must be one of %s" % sorted(_PAPER))
        width, height = _PAPER[key]
        applied.append("paper")
    if "width_mm" in args and "height_mm" in args:
        width, height = _mm100(args["width_mm"]), _mm100(args["height_mm"])
        applied.append("size")

    landscape = None
    if "orientation" in args:
        landscape = str(args["orientation"]).lower() == "landscape"
        applied.append("orientation")

    if width is not None:
        if landscape is None:
            landscape = bool(style.IsLandscape)
        if landscape and width < height:
            width, height = height, width
        elif landscape is False and width > height:
            width, height = height, width
        size = _uno_struct("com.sun.star.awt.Size")
        size.Width, size.Height = width, height
        style.Size = size
        style.IsLandscape = bool(landscape)
    elif landscape is not None:
        cur = style.Size
        if (landscape and cur.Width < cur.Height) or \
           (not landscape and cur.Width > cur.Height):
            size = _uno_struct("com.sun.star.awt.Size")
            size.Width, size.Height = cur.Height, cur.Width
            style.Size = size
        style.IsLandscape = bool(landscape)

    for arg, prop in (("margin_top_mm", "TopMargin"),
                      ("margin_bottom_mm", "BottomMargin"),
                      ("margin_left_mm", "LeftMargin"),
                      ("margin_right_mm", "RightMargin")):
        if arg in args:
            setattr(style, prop, _mm100(args[arg]))
            applied.append(arg)

    if "columns" in args:
        cols = doc.createInstance("com.sun.star.text.TextColumns")
        cols.setColumnCount(int(args["columns"]))
        style.TextColumns = cols
        applied.append("columns")

    if not applied:
        raise RuntimeError("Give at least one page property: paper, width_mm+"
                           "height_mm, orientation, margin_*_mm, columns.")
    return {"page_style": style.Name, "applied": applied}


def tool_writer_set_header_footer(args):
    doc = _require_writer()
    style = _page_style(doc, args.get("style_name"))
    which = str(args.get("which", "header")).lower()
    if which not in ("header", "footer"):
        raise RuntimeError("which must be 'header' or 'footer'.")
    on_prop = "HeaderIsOn" if which == "header" else "FooterIsOn"
    text_prop = "HeaderText" if which == "header" else "FooterText"

    enable = bool(args.get("enable", True))
    setattr(style, on_prop, enable)
    if not enable:
        return {"page_style": style.Name, which: "disabled"}
    if "text" in args:
        htext = getattr(style, text_prop)
        htext.setString(args["text"])
    return {"page_style": style.Name, which: "enabled",
            "text": args.get("text", "")}


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


# --------------------------------------------------------------------------- #
# Tools — form controls (buttons and other UI elements)
# --------------------------------------------------------------------------- #

_FORM_COMPONENTS = {
    "button": "com.sun.star.form.component.CommandButton",
    "checkbox": "com.sun.star.form.component.CheckBox",
    "textfield": "com.sun.star.form.component.TextField",
    "label": "com.sun.star.form.component.FixedText",
    "listbox": "com.sun.star.form.component.ListBox",
}


def tool_insert_form_control(args):
    ub = _bridge()
    doc = _current_doc()
    kind = str(args.get("kind", "button")).lower()
    service = _FORM_COMPONENTS.get(kind)
    if service is None:
        raise RuntimeError("kind must be one of %s" % sorted(_FORM_COMPONENTS))

    model = doc.createInstance(service)
    if kind in ("button", "checkbox", "label") and "label" in args:
        model.Label = args["label"]
    if kind == "textfield" and "text" in args:
        model.DefaultText = args["text"]
    if kind == "listbox" and args.get("items"):
        model.StringItemList = tuple(str(x) for x in args["items"])
        model.Dropdown = True
    if kind == "button" and args.get("url"):
        model.ButtonType = _uno_enum("com.sun.star.form.FormButtonType", "URL")
        model.TargetURL = args["url"]
    if args.get("name"):
        model.Name = args["name"]

    shape = doc.createInstance("com.sun.star.drawing.ControlShape")
    size = _uno_struct("com.sun.star.awt.Size")
    size.Width = _mm100(args.get("width_mm", 40))
    size.Height = _mm100(args.get("height_mm", 10))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
    pos.X = _mm100(args.get("x_mm", 10))
    pos.Y = _mm100(args.get("y_mm", 10))
    shape.setPosition(pos)
    shape.setControl(model)

    if ub.is_calc(doc):
        draw_page = doc.getCurrentController().getActiveSheet().getDrawPage()
    else:
        draw_page = doc.getDrawPage()
    draw_page.add(shape)
    return {"inserted": kind, "name": model.Name,
            "label": args.get("label", args.get("text", ""))}


# --------------------------------------------------------------------------- #
# Tool registry + JSON schemas
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Tools — automation & inspection (the Kahatayn-session wishlist)
# --------------------------------------------------------------------------- #

def tool_reload_document(args):
    """store -> close -> load from disk. THE verification step for anything
    that touches shapes/macros: the in-memory model can lie (e.g. form-control
    shapes silently dropped by the ODS writer on RTL sheets); only a reload
    shows what actually serialized."""
    import time
    doc = _current_doc()
    url = doc.getURL()
    if not url:
        raise RuntimeError("The active document has no file URL — save it first.")
    if args.get("save", True):
        doc.store()
    doc.close(False)
    time.sleep(1.0)
    # 4 = MacroExecMode.ALWAYS_EXECUTE_NO_WARN (trusted-location workflows)
    newdoc = _desktop().loadComponentFromURL(url, "_blank", 0,
                                             (_pv("MacroExecutionMode", 4),))
    if newdoc is None:
        raise RuntimeError("Reload failed: loadComponentFromURL returned None for %s" % url)
    time.sleep(1.0)
    return {"reloaded": _doc_info(newdoc)}


def tool_run_macro(args):
    """Invoke a macro in the active document. 'name' is 'Library.Module.Sub'
    (document Basic), 'Module.Sub' (library defaults to Standard), or a full
    vnd.sun.star.script: URI. Returns the macro's return value."""
    doc = _current_doc()
    name = str(args["name"])
    if name.startswith("vnd.sun.star.script:"):
        uri = name
    else:
        parts = name.split(".")
        if len(parts) == 2:
            name = "Standard." + name
        elif len(parts) != 3:
            raise RuntimeError("Give 'Library.Module.Sub', 'Module.Sub', or a "
                               "full vnd.sun.star.script: URI — got %r" % name)
        uri = ("vnd.sun.star.script:%s?language=Basic&location=document" % name)
    script = doc.getScriptProvider().getScript(uri)
    invoked = script.invoke(tuple(args.get("args") or ()), (), ())
    ret = invoked[0] if isinstance(invoked, tuple) and invoked else None
    try:
        json.dumps(ret)
    except Exception:
        ret = str(ret)
    return {"invoked": uri, "returned": ret}


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


def tool_basic_module(args):
    """Manage the document's embedded Basic: list libraries/modules, get a
    module's source, or set it (create/replace). After 'set', invoke a no-op
    Sub via run_macro as a compile check — one syntax error silently kills the
    WHOLE module at runtime."""
    doc = _current_doc()
    libs = doc.BasicLibraries
    action = args.get("action") or "list"
    if action == "list":
        out = {}
        for ln in libs.getElementNames():
            try:
                libs.loadLibrary(ln)
                lib = libs.getByName(ln)
                out[ln] = {m: len(lib.getByName(m)) for m in lib.getElementNames()}
            except Exception as exc:
                out[ln] = "unreadable: %s" % exc
        return {"libraries": out}
    library, module = args.get("library"), args.get("module")
    if not library or not module:
        raise RuntimeError("'library' and 'module' are required for %s." % action)
    if action == "get":
        libs.loadLibrary(library)
        return {"library": library, "module": module,
                "source": libs.getByName(library).getByName(module)}
    if action == "set":
        source = args.get("source")
        if source is None:
            raise RuntimeError("'source' is required for set.")
        if not libs.hasByName(library):
            libs.createLibrary(library)
        else:
            libs.loadLibrary(library)
        lib = libs.getByName(library)
        if lib.hasByName(module):
            lib.replaceByName(module, source)
        else:
            lib.insertByName(module, source)
        return {"library": library, "module": module, "chars": len(source)}
    raise RuntimeError("Unknown action %r — use list, get or set." % action)


def tool_inspect_ods(args):
    """Grep inside the SAVED file's zip entries (content.xml by default) — the
    ground truth of what serialized, independent of the in-memory model. This
    is how the RTL dropped-form-controls root cause was found."""
    import re
    import zipfile
    path = args.get("path")
    if not path:
        url = _current_doc().getURL()
        if not url:
            raise RuntimeError("No 'path' given and the active document has no file URL.")
        import unohelper
        path = unohelper.fileUrlToSystemPath(url)
    pattern = args["pattern"]
    entry = args.get("entry") or "content.xml"
    ctx_chars = int(args.get("context") or 120)
    limit = int(args.get("max_matches") or 10)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if entry not in names:
            raise RuntimeError("No entry %r in %s. Entries: %s"
                               % (entry, path, ", ".join(names[:25])))
        text = z.read(entry).decode("utf-8", "replace")
    excerpts = []
    total = 0
    for m in re.finditer(pattern, text):
        total += 1
        if len(excerpts) < limit:
            start = max(0, m.start() - ctx_chars)
            excerpts.append(text[start:m.end() + ctx_chars])
    return {"path": path, "entry": entry, "pattern": pattern,
            "match_count": total, "excerpts": excerpts}


def tool_uno_exec(args):
    """Escape hatch: run a short Python snippet against the live UNO bridge.
    In scope: ctx, smgr, desktop, doc (active document or None), uno.
    Captured stdout is returned; set a variable named `result` for a value."""
    import contextlib
    import io as _io
    code = args["code"]
    state = _connect()
    doc = None
    try:
        doc = _current_doc()
    except Exception:
        pass
    import uno as _uno
    scope = {"ctx": state["ctx"], "smgr": state["smgr"],
             "desktop": state["desktop"], "doc": doc, "uno": _uno,
             "result": None}
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "<uno_exec>", "exec"), scope)
    out = {"stdout": buf.getvalue()[-8000:]}
    if scope.get("result") is not None:
        try:
            json.dumps(scope["result"])
            out["result"] = scope["result"]
        except Exception:
            out["result"] = str(scope["result"])
    return out


# --------------------------------------------------------------------------- #
# Tools — "Good first tools" (single-API wrappers, see docs/TOOLS-WANTED.md)
# --------------------------------------------------------------------------- #

def _calc_axis(sheet, axis):
    """'columns'|'rows' -> the sheet's column/row collection. Raises on typos."""
    a = str(axis).lower()
    if a in ("columns", "column", "col", "cols"):
        return sheet.getColumns(), "columns"
    if a in ("rows", "row"):
        return sheet.getRows(), "rows"
    raise RuntimeError("axis must be 'columns' or 'rows', got: %r" % axis)


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


def tool_calc_recalculate(args):
    doc = _require_calc()
    hard = bool(args.get("hard", True))
    if hard:
        doc.calculateAll()
    else:
        doc.calculate()
    return {"recalculated": "all" if hard else "dirty"}


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


def tool_writer_word_count(_args):
    doc = _require_writer()
    out = {}
    for key, prop in (("words", "WordCount"), ("paragraphs", "ParagraphCount"),
                      ("characters", "CharacterCount")):
        try:
            out[key] = int(doc.getPropertyValue(prop))
        except Exception:
            out[key] = None
    if any(out[k] is None for k in ("words", "paragraphs", "characters")):
        try:
            stats = {nv.Name: nv.Value
                     for nv in doc.getDocumentProperties().DocumentStatistics}
            for key, prop in (("words", "WordCount"),
                              ("paragraphs", "ParagraphCount"),
                              ("characters", "CharacterCount")):
                if out[key] is None and prop in stats:
                    out[key] = int(stats[prop])
        except Exception:
            pass
    try:
        out["pages"] = int(doc.getCurrentController().PageCount)
    except Exception:
        out["pages"] = None
    return out


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


def tool_writer_get_paragraphs(_args):
    doc = _require_writer()
    out = []
    enum = doc.getText().createEnumeration()
    i = 0
    while enum.hasMoreElements():
        para = enum.nextElement()
        try:
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
        except Exception:
            continue
        try:
            level = int(para.getPropertyValue("OutlineLevel"))
        except Exception:
            level = 0
        try:
            style = para.getPropertyValue("ParaStyleName")
        except Exception:
            style = None
        out.append({"index": i, "text": para.getString(),
                    "style": style, "is_heading": level > 0})
        i += 1
    return {"paragraphs": out}


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        import uno
        if isinstance(v, uno.Enum):          # e.g. CellHoriJustify -> 'CENTER'
            return v.value
    except Exception:
        pass
    return str(v)


def tool_get_document_properties(_args):
    doc = _current_doc()
    props = doc.getDocumentProperties()

    def _dt(d):
        try:
            if not (d.Year or d.Month or d.Day):
                return None
            return ("%04d-%02d-%02dT%02d:%02d:%02d"
                    % (d.Year, d.Month, d.Day, d.Hours, d.Minutes, d.Seconds))
        except Exception:
            return None

    kw = props.Keywords
    out = {
        "title": props.Title, "author": props.Author,
        "subject": props.Subject,
        "keywords": list(kw) if not isinstance(kw, str) else kw,
        "description": props.Description,
        "generator": getattr(props, "Generator", None),
        "modified_by": props.ModifiedBy,
        "created": _dt(props.CreationDate),
        "modified": _dt(props.ModificationDate),
    }
    try:
        out["statistics"] = {nv.Name: nv.Value
                             for nv in props.DocumentStatistics}
    except Exception:
        out["statistics"] = {}
    try:
        udp = props.UserDefinedProperties
        names = [p.Name for p in udp.getPropertySetInfo().getProperties()]
        out["custom"] = {n: _jsonable(udp.getPropertyValue(n)) for n in names}
    except Exception:
        out["custom"] = {}
    return out


def tool_set_document_modified(args):
    doc = _current_doc()
    if args.get("modified") is not None:
        doc.setModified(bool(args["modified"]))
    return {"modified": bool(doc.isModified())}


# --------------------------------------------------------------------------- #
# Tools — Writer P1 (see docs/TOOLS-WANTED.md)
# --------------------------------------------------------------------------- #

def _enum_value(v):
    """pyuno Enum -> its string value (e.g. 'AT_PARAGRAPH'); str() otherwise."""
    return getattr(v, "value", None) or str(v)


def tool_writer_list_objects(_args):
    doc = _require_writer()
    out = []

    def _named(kind, getter):
        try:
            coll = getter()
            names = coll.getElementNames()
        except Exception:
            return
        for nm in names:
            try:
                obj = coll.getByName(nm)
            except Exception:
                continue
            entry = {"kind": kind, "name": nm}
            try:
                entry["anchor"] = _enum_value(obj.AnchorType)
            except Exception:
                pass
            try:
                entry["size_mm"] = [round(obj.Size.Width / 100.0, 1),
                                    round(obj.Size.Height / 100.0, 1)]
            except Exception:
                pass
            out.append(entry)

    _named("graphic", doc.getGraphicObjects)
    _named("frame", doc.getTextFrames)
    _named("embedded", doc.getEmbeddedObjects)

    # Draw shapes (rectangle/ellipse/line/text/custom) live only on the draw
    # page — they were previously invisible to discovery. Skip the graphics/OLE
    # already listed by name above so nothing double-counts.
    seen = {e["name"] for e in out if e.get("name")}
    try:
        dp = doc.getDrawPage()
    except Exception:
        dp = None
    for i in range(dp.getCount() if dp else 0):
        try:
            shp = dp.getByIndex(i)
            st = getattr(shp, "ShapeType", "") or ""
        except Exception:
            continue
        if ("GraphicObjectShape" in st or "OLE2Shape" in st
                or "FrameShape" in st):
            continue
        nm = getattr(shp, "Name", "") or ""
        if nm and nm in seen:
            continue
        entry = {"kind": "shape", "name": nm, "type": st}
        try:
            entry["anchor"] = _enum_value(shp.AnchorType)
        except Exception:
            pass
        try:
            entry["size_mm"] = [round(shp.Size.Width / 100.0, 1),
                                round(shp.Size.Height / 100.0, 1)]
        except Exception:
            pass
        out.append(entry)
    return {"objects": out, "count": len(out)}


def _writer_paragraphs(doc):
    """Yield (index, paragraph) over body paragraphs only — the same index space
    writer_get_paragraphs reports."""
    enum = doc.getText().createEnumeration()
    i = 0
    while enum.hasMoreElements():
        para = enum.nextElement()
        try:
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
        except Exception:
            continue
        yield i, para
        i += 1


def tool_writer_set_paragraph_text(args):
    doc = _require_writer()
    target = int(args["index"])
    text = doc.getText()
    for i, para in _writer_paragraphs(doc):
        if i == target:
            cursor = text.createTextCursorByRange(para.getStart())
            cursor.gotoEndOfParagraph(True)
            cursor.setString(args["text"])   # single paragraph; no break handling
            return {"index": target, "text": args["text"]}
    raise RuntimeError("No body paragraph at index %d." % target)


def _set_para_direction(para, wm, adjust_key, do_align):
    # WritingMode2 short: RL_TB=1 (rtl) / LR_TB=0 (ltr).
    para.WritingMode = wm
    if do_align:
        para.ParaAdjust = _uno_enum("com.sun.star.style.ParagraphAdjust",
                                    adjust_key)


def tool_writer_set_text_direction(args):
    doc = _require_writer()
    direction = str(args.get("direction", "rtl")).lower()
    if direction not in ("rtl", "ltr"):
        raise RuntimeError("direction must be 'rtl' or 'ltr'.")
    wm = 1 if direction == "rtl" else 0
    adjust_key = "RIGHT" if direction == "rtl" else "LEFT"
    do_align = bool(args.get("align", True))

    # Targeted mode: only body paragraphs [start, start+count). Leaves tables
    # and the page style untouched.
    if "start" in args or "count" in args:
        start = int(args.get("start", 0))
        cnt = args.get("count")
        done = 0
        for i, para in _writer_paragraphs(doc):
            if i < start:
                continue
            if cnt is not None and i >= start + int(cnt):
                break
            _set_para_direction(para, wm, adjust_key, do_align)
            done += 1
        return {"direction": direction, "scope": "range", "paragraphs": done}

    # Whole-document flip: every body paragraph, then (by default) every
    # table-cell paragraph and the page style — the full RTL/LTR recipe.
    paras = 0
    for _, para in _writer_paragraphs(doc):
        _set_para_direction(para, wm, adjust_key, do_align)
        paras += 1

    cells = 0
    if bool(args.get("tables", True)):
        tables = doc.getTextTables()
        for ti in range(tables.getCount()):
            table = tables.getByIndex(ti)
            for cn in table.getCellNames():
                try:
                    cenum = table.getCellByName(cn).createEnumeration()
                except Exception:
                    continue
                while cenum.hasMoreElements():
                    cpar = cenum.nextElement()
                    try:
                        if cpar.supportsService("com.sun.star.text.Paragraph"):
                            _set_para_direction(cpar, wm, adjust_key, do_align)
                            cells += 1
                    except Exception:
                        pass

    page = False
    if bool(args.get("page", True)):
        try:
            _page_style(doc, args.get("style_name")).WritingMode = wm
            page = True
        except Exception:
            pass

    return {"direction": direction, "scope": "document", "paragraphs": paras,
            "table_cell_paragraphs": cells, "page_style_set": page}


def tool_writer_delete_paragraphs(args):
    doc = _require_writer()
    start = int(args["start"])
    count = int(args.get("count", 1))
    if count < 1:
        raise RuntimeError("count must be >= 1.")
    paras = [p for _, p in _writer_paragraphs(doc)]
    n = len(paras)
    if start < 0 or start >= n:
        raise RuntimeError("No body paragraph at index %d (document has %d)."
                           % (start, n))
    end = min(start + count, n)          # exclusive; clamp to the last paragraph
    deleted = end - start
    text = doc.getText()
    if start == 0 and end == n:
        # Text must keep one paragraph — collapse everything to a single empty one.
        cur = text.createTextCursorByRange(text.getStart())
        cur.gotoRange(text.getEnd(), True)
        cur.setString("")
        return {"deleted": deleted, "remaining": 1,
                "note": "all paragraphs removed; one empty paragraph remains"}
    if end < n:
        # Consume paras[start..end-1] and their trailing breaks; paras[end]
        # becomes the new paragraph at 'start'.
        left, right = paras[start].getStart(), paras[end].getStart()
    else:
        # Deleting through the last paragraph: also consume the break BEFORE
        # 'start' so paras[start-1] becomes the final paragraph.
        left, right = paras[start - 1].getEnd(), paras[n - 1].getEnd()
    cur = text.createTextCursorByRange(left)
    cur.gotoRange(right, True)
    cur.setString("")
    return {"deleted": deleted, "start": start, "remaining": n - deleted}


_FIELD_SERVICES = {
    "page_number": "com.sun.star.text.TextField.PageNumber",
    "page_count": "com.sun.star.text.TextField.PageCount",
    "date": "com.sun.star.text.TextField.DateTime",
    "time": "com.sun.star.text.TextField.DateTime",
    "title": "com.sun.star.text.TextField.DocInfo.Title",
    "author": "com.sun.star.text.TextField.Author",
}


def tool_writer_insert_field(args):
    doc = _require_writer()
    kind = str(args.get("field", "page_number")).lower()
    if kind not in _FIELD_SERVICES:
        raise RuntimeError("field must be one of %s" % sorted(_FIELD_SERVICES))
    field = doc.createInstance(_FIELD_SERVICES[kind])
    if kind in ("date", "time"):
        try:
            field.IsDate = (kind == "date")
            field.IsFixed = bool(args.get("fixed", False))
        except Exception:
            pass
    if bool(args.get("new_paragraph", False)):
        text, cursor = _append_paragraph(doc, style="Standard")
    else:
        text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, field, False)
    return {"inserted_field": kind}


def tool_writer_insert_toc(args):
    doc = _require_writer()
    toc = doc.createInstance("com.sun.star.text.ContentIndex")
    for prop, value in (("CreateFromOutline", True),
                        ("Title", args.get("title")),
                        ("Level", args.get("levels"))):
        if value is None:
            continue
        try:
            setattr(toc, prop, int(value) if prop == "Level" else value)
        except Exception:
            pass
    text = doc.getText()
    if bool(args.get("at_start", False)):
        cursor = text.createTextCursorByRange(text.getStart())
    else:
        text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, toc, False)
    try:
        toc.update()
    except Exception:
        pass
    return {"inserted": "table_of_contents"}


def tool_writer_update_indexes(_args):
    doc = _require_writer()
    indexes = 0
    try:
        idxs = doc.getDocumentIndexes()
        for i in range(idxs.getCount()):
            idxs.getByIndex(i).update()
            indexes += 1
    except Exception:
        pass
    try:
        doc.getTextFields().refresh()
    except Exception:
        pass
    return {"indexes_updated": indexes, "fields_refreshed": True}


def _make_numbering_rules(doc, ordered):
    """A bullet (default) or ordered NumberingRules, applied directly to
    paragraphs so lists work regardless of the build's localized list-STYLE
    names (e.g. 'List 1' / 'Numbering 1' instead of 'List Bullet')."""
    import uno
    from com.sun.star.style.NumberingType import ARABIC, CHAR_SPECIAL
    rules = doc.createInstance("com.sun.star.text.NumberingRules")
    if ordered:
        level = (_pv("NumberingType", ARABIC), _pv("Prefix", ""),
                 _pv("Suffix", "."))
    else:
        level = (_pv("NumberingType", CHAR_SPECIAL),
                 _pv("BulletChar", u"•"), _pv("BulletFontName", "OpenSymbol"),
                 _pv("Prefix", ""), _pv("Suffix", ""))
    uno.invoke(rules, "replaceByIndex",
               (0, _any_seq("com.sun.star.beans.PropertyValue", level)))
    return rules


def tool_writer_apply_list(args):
    doc = _require_writer()
    ordered = bool(args.get("ordered", False))
    start = int(args.get("start", 0))
    count = args.get("count")
    end = start + int(count) - 1 if count is not None else None
    rules = _make_numbering_rules(doc, ordered)
    changed = matched = 0
    last_err = None
    for i, para in _writer_paragraphs(doc):
        if i >= start and (end is None or i <= end):
            matched += 1
            try:
                para.NumberingRules = rules
                para.NumberingLevel = 0
                changed += 1
            except Exception as exc:
                last_err = exc
    if matched == 0:
        raise RuntimeError("No body paragraphs in range (start=%d, count=%s)."
                           % (start, count))
    if changed == 0:
        # matched paragraphs but none took the list — surface, don't no-op.
        raise RuntimeError("Matched %d paragraph(s) but could not apply the list"
                           "%s." % (matched,
                                    " (%s)" % type(last_err).__name__ if last_err
                                    else ""))
    return {"ordered": ordered, "paragraphs_changed": changed,
            "paragraphs_matched": matched}


# --------------------------------------------------------------------------- #
# Tools — cross-cutting (Calc & Writer) — see docs/TOOLS-WANTED.md
# --------------------------------------------------------------------------- #

def _dispatch(doc, command, props=()):
    """Execute a .uno: command against the document's frame."""
    state = _connect()
    helper = state["smgr"].createInstanceWithContext(
        "com.sun.star.frame.DispatchHelper", state["ctx"])
    frame = doc.getCurrentController().getFrame()
    return helper.executeDispatch(frame, command, "", 0, tuple(props))


# friendly family token -> UNO StyleFamilies name
_STYLE_FAMILIES = {
    "paragraph": "ParagraphStyles", "character": "CharacterStyles",
    "cell": "CellStyles", "page": "PageStyles", "frame": "FrameStyles",
    "numbering": "NumberingStyles", "graphic": "GraphicStyles",
    "table": "TableStyles",
}


def _resolve_style_family(available, fam):
    if fam in available:
        return fam
    key = str(fam).strip().lower().rstrip("s")
    if key in _STYLE_FAMILIES and _STYLE_FAMILIES[key] in available:
        return _STYLE_FAMILIES[key]
    for nm in available:
        if nm.lower() == str(fam).lower():
            return nm
    return None


def _apply_style_props(style, fmt):
    if "bold" in fmt:
        style.CharWeight = 150.0 if fmt["bold"] else 100.0
    if "italic" in fmt:
        style.CharPosture = _uno_enum("com.sun.star.awt.FontSlant",
                                      "ITALIC" if fmt["italic"] else "NONE")
    if "font_name" in fmt:
        style.CharFontName = fmt["font_name"]
    if "font_size" in fmt:
        style.CharHeight = float(fmt["font_size"])
    if "font_color" in fmt:
        style.CharColor = _hex_color(fmt["font_color"])
    if "background_color" in fmt:
        for prop in ("CellBackColor", "ParaBackColor", "BackColor"):
            try:
                setattr(style, prop, _hex_color(fmt["background_color"]))
                break
            except Exception:
                continue


def tool_set_hyperlink(args):
    doc = _current_doc()
    url = args["url"]
    kind = _doc_kind(doc)
    if kind == "calc":
        sheet = _resolve_sheet(doc, args.get("sheet"))
        cell = sheet.getCellRangeByName(args["cell"])
        display = args.get("text") or cell.getString() or url
        cell.setString("")
        ctext = cell.getText()
        cursor = ctext.createTextCursor()
        field = doc.createInstance("com.sun.star.text.TextField.URL")
        field.URL = url
        field.Representation = display
        ctext.insertTextContent(cursor, field, False)
        return {"cell": args["cell"], "url": url}
    if kind == "writer":
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findAll(desc)
        n = 0
        for i in range(found.getCount()):
            rng = found.getByIndex(i)
            rng.HyperLinkURL = url
            if args.get("target"):
                rng.HyperLinkTarget = args["target"]
            n += 1
        return {"matches_linked": n, "url": url}
    raise RuntimeError("set_hyperlink needs a Calc ('cell') or Writer ('search') document.")


def tool_export_document(args):
    import uno
    doc = _current_doc()
    path = args["path"]
    fmt = str(args.get("format")
              or os.path.splitext(path)[1].lstrip(".")).lower()
    url = _to_url(path)
    if fmt == "pdf":
        fd = []
        if args.get("page_range"):
            fd.append(_pv("PageRange", str(args["page_range"])))
        if args.get("pdfa"):
            fd.append(_pv("SelectPdfVersion", 1))   # PDF/A-1
        if args.get("quality") is not None:
            fd.append(_pv("Quality", int(args["quality"])))
        if args.get("password"):
            fd.append(_pv("EncryptFile", True))
            fd.append(_pv("DocumentOpenPassword", str(args["password"])))
        filter_name = ("writer_pdf_Export" if _doc_kind(doc) == "writer"
                       else "calc_pdf_Export")
        props = [_pv("FilterName", filter_name)]
        if fd:
            props.append(_pv("FilterData",
                             uno.Any("[]com.sun.star.beans.PropertyValue",
                                     tuple(fd))))
        doc.storeToURL(url, tuple(props))
        return {"exported": path, "format": "pdf"}
    if fmt == "csv":
        delim = args.get("delimiter", ",")
        sep = ord(delim[0]) if delim else 44
        encoding = 76  # UTF-8 token in the CSV filter's charset table
        opts = "%d,%d,%d,1" % (sep, ord(args.get("quote", '"')[0]), encoding)
        props = [_pv("FilterName", "Text - txt - csv (StarCalc)"),
                 _pv("FilterOptions", opts)]
        doc.storeToURL(url, tuple(props))
        return {"exported": path, "format": "csv", "filter_options": opts}
    raise RuntimeError("export_document supports format 'pdf' or 'csv', got %r." % fmt)


def tool_set_document_properties(args):
    doc = _current_doc()
    props = doc.getDocumentProperties()
    changed = []
    for key, prop in (("title", "Title"), ("author", "Author"),
                      ("subject", "Subject"), ("description", "Description")):
        if args.get(key) is not None:
            setattr(props, prop, args[key])
            changed.append(prop)
    if args.get("keywords") is not None:
        kw = args["keywords"]
        props.Keywords = tuple(kw) if isinstance(kw, (list, tuple)) else (str(kw),)
        changed.append("Keywords")
    custom = args.get("custom")
    if custom:
        udp = props.UserDefinedProperties
        info = udp.getPropertySetInfo()
        for k, v in custom.items():
            try:
                if info.hasPropertyByName(k):
                    if v is None:
                        udp.removeProperty(k)
                    else:
                        udp.setPropertyValue(k, v)
                elif v is not None:
                    from com.sun.star.beans.PropertyAttribute import REMOVEABLE
                    udp.addProperty(k, REMOVEABLE, v)
            except Exception:
                pass
        changed.append("custom")
    return {"updated": changed}


def tool_list_styles(args):
    doc = _current_doc()
    families = doc.getStyleFamilies()
    available = list(families.getElementNames())
    fam = args.get("family")
    if fam:
        resolved = _resolve_style_family(available, fam)
        if resolved is None:
            raise RuntimeError("No style family %r. Families: %s"
                               % (fam, ", ".join(available)))
        wanted = [resolved]
    else:
        wanted = available
    used_only = bool(args.get("in_use_only", False))
    out = {}
    for f in wanted:
        coll = families.getByName(f)
        names = []
        for nm in coll.getElementNames():
            if used_only:
                try:
                    if not coll.getByName(nm).isInUse():
                        continue
                except Exception:
                    pass
            names.append(nm)
        out[f] = names
    return {"styles": out}


_STYLE_SERVICES = {
    "ParagraphStyles": "com.sun.star.style.ParagraphStyle",
    "CharacterStyles": "com.sun.star.style.CharacterStyle",
    "CellStyles": "com.sun.star.style.CellStyle",
    "PageStyles": "com.sun.star.style.PageStyle",
    "FrameStyles": "com.sun.star.style.FrameStyle",
}


def tool_set_style(args):
    doc = _current_doc()
    families = doc.getStyleFamilies()
    fam = _resolve_style_family(list(families.getElementNames()), args["family"])
    if fam is None:
        raise RuntimeError("No style family %r." % args["family"])
    coll = families.getByName(fam)
    name = args["name"]
    if coll.hasByName(name):
        style = coll.getByName(name)
        created = False
    else:
        service = _STYLE_SERVICES.get(fam)
        if not service:
            raise RuntimeError("Cannot create styles in family %r." % fam)
        style = doc.createInstance(service)
        coll.insertByName(name, style)
        created = True
    if args.get("parent"):
        try:
            style.ParentStyle = args["parent"]
        except Exception:
            pass
    if args.get("follow_style"):
        try:
            style.FollowStyle = args["follow_style"]   # next-paragraph style
        except Exception:
            pass
    _apply_style_props(style, args)
    return {"style": name, "family": fam, "created": created}


def tool_protect_document(args):
    doc = _current_doc()
    kind = _doc_kind(doc)
    protect = bool(args.get("protect", True))
    pwd = args.get("password", "") or ""
    out = {"protect": protect}
    if kind == "calc":
        if args.get("sheet") not in (None, ""):
            target = _resolve_sheet(doc, args["sheet"])
            out["scope"] = "sheet"
        else:
            target = doc
            out["scope"] = "workbook"
        if protect:
            target.protect(pwd)
        else:
            target.unprotect(pwd)
        out["is_protected"] = bool(target.isProtected())
        return out
    if kind == "writer":
        sections = doc.getTextSections()
        n = 0
        for nm in sections.getElementNames():
            sections.getByName(nm).IsProtected = protect
            n += 1
        out["sections_affected"] = n
        return out
    raise RuntimeError("protect_document needs a Calc or Writer document.")


def tool_dispatch_uno(args):
    doc = _current_doc()
    command = args["command"]
    props = tuple(_pv(k, v) for k, v in (args.get("args") or {}).items())
    self_res = _dispatch(doc, command, props)
    return {"dispatched": command, "handled": self_res is not None}


def tool_document_undo(args):
    doc = _current_doc()
    mgr = doc.getUndoManager()
    action = str(args.get("action", "status")).lower()
    if action == "undo":
        if mgr.isUndoPossible():
            mgr.undo()
    elif action == "redo":
        if mgr.isRedoPossible():
            mgr.redo()
    elif action == "clear":
        mgr.clear()
    elif action != "status":
        raise RuntimeError("action must be undo|redo|clear|status.")
    out = {"undo_possible": bool(mgr.isUndoPossible()),
           "redo_possible": bool(mgr.isRedoPossible())}
    try:
        out["undo_title"] = (mgr.getCurrentUndoActionTitle()
                             if mgr.isUndoPossible() else None)
    except Exception:
        out["undo_title"] = None
    return out


def tool_bind_document_event(args):
    import uno
    doc = _current_doc()
    events = doc.getEvents()
    name = args["event"]
    script = args.get("script")
    # The PropertyValue sequence MUST be a typed UNO Any — a bare tuple is
    # rejected with IllegalArgumentException. uno.invoke marshals it correctly.
    if script:
        binding = _any_seq("com.sun.star.beans.PropertyValue",
                           (_pv("EventType", "Script"), _pv("Script", script)))
    else:
        binding = _any_seq("com.sun.star.beans.PropertyValue", ())
    uno.invoke(events, "replaceByName", (name, binding))
    return {"event": name, "bound": bool(script)}


def _zoom_target(ctrl):
    """The object carrying ZoomType/ZoomValue: Calc's controller exposes them
    directly; Writer's live on ctrl.ViewSettings. (Writing ctrl.ZoomValue on
    Writer raised AttributeError — the original bug.)"""
    if hasattr(ctrl, "ZoomValue"):
        return ctrl
    for get in (lambda: ctrl.ViewSettings, lambda: ctrl.getViewSettings()):
        try:
            vs = get()
        except Exception:
            vs = None
        if vs is not None and hasattr(vs, "ZoomValue"):
            return vs
    return None


def tool_set_view_zoom(args):
    doc = _current_doc()
    ctrl = doc.getCurrentController()
    # ZoomType is a com.sun.star.view.DocumentZoomType short.
    vs = _zoom_target(ctrl)
    if vs is None:
        raise RuntimeError("The active view exposes no zoom settings "
                           "(headless sessions have no view).")
    zoom_types = {"optimal": 0, "page_width": 1, "whole_page": 2,
                  "percent": 3, "page_width_exact": 4}
    if args.get("percent") is not None:
        vs.ZoomType = 3                       # BY_VALUE
        vs.ZoomValue = int(args["percent"])
    elif args.get("type"):
        key = str(args["type"]).lower()
        if key not in zoom_types:
            raise RuntimeError("type must be one of %s." % sorted(zoom_types))
        vs.ZoomType = zoom_types[key]
    else:
        raise RuntimeError("Provide 'percent' and/or 'type'.")
    return {"zoom_type": int(vs.ZoomType), "zoom_value": int(vs.ZoomValue)}


def tool_get_signatures(_args):
    doc = _current_doc()
    out = {"signed": False, "valid": None, "signer": None, "date": None}
    url = doc.getURL()
    if not url:
        out["note"] = "Document has no file yet — nothing to verify."
        return out
    state = _connect()
    try:
        dds = state["smgr"].createInstanceWithContext(
            "com.sun.star.security.DocumentDigitalSignatures", state["ctx"])
        # verifyDocumentContentSignatures wants an XStorage — a URL string raises
        # CannotConvertException. Open the doc as a read-only storage first.
        try:
            from com.sun.star.embed.ElementModes import READ
            sf = state["smgr"].createInstanceWithContext(
                "com.sun.star.embed.StorageFactory", state["ctx"])
            storage = sf.createInstanceWithArguments((url, READ))
            infos = dds.verifyDocumentContentSignatures(storage, None)
        except Exception:
            infos = dds.verifyDocumentContentSignatures(url, None)  # legacy overload
    except Exception as exc:
        out["note"] = "Could not read signatures (%s)." % type(exc).__name__
        return out
    out["signed"] = bool(infos)
    if infos:
        first = infos[0]
        try:
            out["valid"] = (int(getattr(first, "SignatureIsValid", 0)) == 1
                            or bool(getattr(first, "SignatureIsValid", False)))
        except Exception:
            pass
        try:
            out["signer"] = first.Signer.SubjectName
        except Exception:
            pass
        try:
            d = first.SignatureDate
            out["date"] = "%04d-%02d-%02d" % (d.Year, d.Month, d.Day)
        except Exception:
            pass
    return out


def tool_list_embedded_objects(_args):
    doc = _current_doc()
    kind = _doc_kind(doc)
    out = []
    if kind == "writer":
        for tag, getter in (("graphic", doc.getGraphicObjects),
                            ("embedded", doc.getEmbeddedObjects)):
            try:
                coll = getter()
                for nm in coll.getElementNames():
                    obj = coll.getByName(nm)
                    e = {"kind": tag, "name": nm}
                    try:
                        e["size_mm"] = [round(obj.Size.Width / 100.0, 1),
                                        round(obj.Size.Height / 100.0, 1)]
                    except Exception:
                        pass
                    out.append(e)
            except Exception:
                pass
    elif kind == "calc":
        sheets = doc.getSheets()
        for si in range(sheets.getCount()):
            sheet = sheets.getByIndex(si)
            dp = sheet.DrawPage
            for i in range(dp.getCount()):
                shp = dp.getByIndex(i)
                st = getattr(shp, "ShapeType", "") or ""
                if "Graphic" in st or "OLE" in st:
                    out.append({"kind": st, "name": getattr(shp, "Name", ""),
                                "sheet": sheet.getName()})
    else:
        raise RuntimeError("list_embedded_objects needs a Calc or Writer document.")
    return {"objects": out, "count": len(out)}


def tool_insert_ole_object(args):
    doc = _current_doc()
    kind = _doc_kind(doc)
    clsid = args.get("clsid")
    obj_kind = str(args.get("object", "math")).lower()
    # Well-known CLSIDs (LibreOffice component GUIDs).
    clsids = {
        "math": "078B7ABA-54FC-457F-8551-6147E776A997",
        "calc": "47BBB4CB-CE4C-4E80-A591-42D9AE74950F",
        "chart": "12DCAE26-281F-416F-A234-C3086127382E",
    }
    if not clsid:
        clsid = clsids.get(obj_kind)
        if not clsid:
            raise RuntimeError("Provide 'clsid' or object in %s." % sorted(clsids))
    if kind == "writer":
        obj = doc.createInstance("com.sun.star.text.TextEmbeddedObject")
        obj.CLSID = clsid
        text, cursor = _writer_end_cursor(doc)
        text.insertTextContent(cursor, obj, False)
        return {"inserted": obj_kind, "clsid": clsid}
    if kind == "calc":
        sheet = _resolve_sheet(doc, args.get("sheet"))
        shape = doc.createInstance("com.sun.star.drawing.OLE2Shape")
        shape.CLSID = clsid
        sheet.DrawPage.add(shape)
        pos = _uno_struct("com.sun.star.awt.Size")
        pos.Width = _mm100(args.get("width_mm", 60))
        pos.Height = _mm100(args.get("height_mm", 40))
        shape.setSize(pos)
        return {"inserted": obj_kind, "clsid": clsid}
    raise RuntimeError("insert_ole_object needs a Calc or Writer document.")


# --------------------------------------------------------------------------- #
# Tools — Writer P2/P3 — see docs/TOOLS-WANTED.md
# --------------------------------------------------------------------------- #

def _writer_find_first(doc, search, match_case=False):
    desc = doc.createSearchDescriptor()
    desc.SearchString = search
    desc.setPropertyValue("SearchCaseSensitive", bool(match_case))
    return doc.findFirst(desc)


_DRAW_SHAPES = {"rectangle": "com.sun.star.drawing.RectangleShape",
                "ellipse": "com.sun.star.drawing.EllipseShape",
                "line": "com.sun.star.drawing.LineShape",
                "text": "com.sun.star.drawing.TextShape"}

_ANCHOR_TYPES = {"as_char": "AS_CHARACTER", "char": "AT_CHARACTER",
                 "paragraph": "AT_PARAGRAPH", "page": "AT_PAGE",
                 "frame": "AT_FRAME"}

_WRAP_MODES = {"none": "NONE", "through": "THROUGH", "parallel": "PARALLEL",
               "dynamic": "DYNAMIC", "left": "LEFT", "right": "RIGHT"}


def tool_writer_delete_object(args):
    doc = _require_writer()
    name = args["name"]
    for getter in (doc.getGraphicObjects, doc.getTextFrames,
                   doc.getEmbeddedObjects):
        try:
            coll = getter()
        except Exception:
            continue
        if coll.hasByName(name):
            obj = coll.getByName(name)
            try:
                doc.getText().removeTextContent(obj)
            except Exception:
                obj.dispose()
            return {"deleted": name}
    try:
        dp = doc.getDrawPage()
        for i in range(dp.getCount()):
            shp = dp.getByIndex(i)
            if getattr(shp, "Name", None) == name:
                dp.remove(shp)
                return {"deleted": name, "kind": "shape"}
    except Exception:
        pass
    sections = doc.getTextSections()
    if sections.hasByName(name):
        doc.getText().removeTextContent(sections.getByName(name))
        return {"deleted": name, "kind": "section"}
    raise RuntimeError("No object named %r found." % name)


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


def tool_writer_set_image_layout(args):
    doc = _require_writer()
    name = args["name"]
    obj = None
    for getter in (doc.getGraphicObjects, doc.getTextFrames):
        coll = getter()
        if coll.hasByName(name):
            obj = coll.getByName(name)
            break
    if obj is None:
        raise RuntimeError("No image or frame named %r." % name)
    if args.get("anchor"):
        a = _ANCHOR_TYPES.get(str(args["anchor"]).lower())
        if not a:
            raise RuntimeError("anchor must be one of %s." % sorted(_ANCHOR_TYPES))
        obj.AnchorType = _uno_enum("com.sun.star.text.TextContentAnchorType", a)
    if args.get("wrap"):
        w = _WRAP_MODES.get(str(args["wrap"]).lower())
        if not w:
            raise RuntimeError("wrap must be one of %s." % sorted(_WRAP_MODES))
        obj.TextWrap = _uno_enum("com.sun.star.text.WrapTextMode", w)
    if args.get("x_mm") is not None:
        obj.HoriOrient = 0
        obj.HoriOrientPosition = _mm100(args["x_mm"])
    if args.get("y_mm") is not None:
        obj.VertOrient = 0
        obj.VertOrientPosition = _mm100(args["y_mm"])
    return {"name": name, "anchor": _enum_value(obj.AnchorType)}


def tool_writer_add_section(args):
    doc = _require_writer()
    section = doc.createInstance("com.sun.star.text.TextSection")
    if args.get("columns"):
        cols = doc.createInstance("com.sun.star.text.TextColumns")
        cols.setColumnCount(int(args["columns"]))
        section.TextColumns = cols
    if args.get("protected"):
        section.IsProtected = True
    text = doc.getText()
    cursor = text.createTextCursorByRange(text.getEnd())
    if args.get("text"):
        text.insertString(cursor, args["text"], False)
        cursor.goLeft(len(args["text"]), True)
    text.insertTextContent(cursor, section, bool(args.get("text")))
    try:
        section.Name = args["name"]
    except Exception:
        pass
    return {"section": getattr(section, "Name", args["name"])}


def tool_writer_bookmarks(args):
    doc = _require_writer()
    action = str(args.get("action", "list")).lower()
    marks = doc.getBookmarks()
    if action == "list":
        out = []
        for nm in marks.getElementNames():
            try:
                txt = marks.getByName(nm).getAnchor().getString()
            except Exception:
                txt = ""
            out.append({"name": nm, "text": txt})
        return {"bookmarks": out}
    name = args["name"]
    if action == "insert":
        bm = doc.createInstance("com.sun.star.text.Bookmark")
        bm.Name = name
        text = doc.getText()
        if args.get("search"):
            rng = _writer_find_first(doc, args["search"],
                                     args.get("match_case", False))
            if rng is None:
                raise RuntimeError("Search text %r not found." % args["search"])
            cursor = text.createTextCursorByRange(rng)
        else:
            cursor = text.createTextCursorByRange(text.getEnd())
        text.insertTextContent(cursor, bm, bool(args.get("search")))
        return {"inserted_bookmark": name}
    if not marks.hasByName(name):
        raise RuntimeError("No bookmark named %r." % name)
    if action == "delete":
        doc.getText().removeTextContent(marks.getByName(name))
        return {"deleted": name}
    if action == "get":
        return {"name": name,
                "text": marks.getByName(name).getAnchor().getString()}
    if action == "set":
        marks.getByName(name).getAnchor().setString(args.get("text", ""))
        return {"name": name, "text": args.get("text", "")}
    raise RuntimeError("action must be list|insert|delete|get|set.")


def tool_writer_insert_cross_reference(args):
    doc = _require_writer()
    from com.sun.star.text.ReferenceFieldSource import BOOKMARK, REFERENCE_MARK
    from com.sun.star.text.ReferenceFieldPart import PAGE, TEXT, NUMBER
    field = doc.createInstance("com.sun.star.text.textfield.GetReference")
    src = str(args.get("source", "bookmark")).lower()
    field.ReferenceFieldSource = BOOKMARK if src == "bookmark" else REFERENCE_MARK
    parts = {"page": PAGE, "text": TEXT, "number": NUMBER}
    field.ReferenceFieldPart = parts.get(str(args.get("show", "page")).lower(),
                                         PAGE)
    field.SourceName = args["target"]
    text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, field, False)
    try:
        doc.getTextFields().refresh()
    except Exception:
        pass
    return {"reference_to": args["target"], "source": src}


def tool_writer_insert_footnote(args):
    doc = _require_writer()
    kind = str(args.get("kind", "footnote")).lower()
    service = ("com.sun.star.text.Endnote" if kind == "endnote"
               else "com.sun.star.text.Footnote")
    note = doc.createInstance(service)
    text = doc.getText()
    if args.get("search"):
        rng = _writer_find_first(doc, args["search"], args.get("match_case", False))
        if rng is None:
            raise RuntimeError("Search text %r not found." % args["search"])
        cursor = text.createTextCursorByRange(rng.getEnd())
    else:
        cursor = text.createTextCursorByRange(text.getEnd())
    text.insertTextContent(cursor, note, False)
    if args.get("text"):
        ntext = note.getText()
        ntext.insertString(ntext.createTextCursor(), args["text"], False)
    return {"inserted": kind}


def tool_writer_insert_shape(args):
    doc = _require_writer()
    kind = str(args.get("kind", "rectangle")).lower()
    service = _DRAW_SHAPES.get(kind)
    if not service:
        raise RuntimeError("kind must be one of %s." % sorted(_DRAW_SHAPES))
    shape = doc.createInstance(service)
    doc.getDrawPage().add(shape)
    size = _uno_struct("com.sun.star.awt.Size")
    size.Width = _mm100(args.get("width_mm", 40))
    size.Height = _mm100(args.get("height_mm", 20))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
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
    return {"inserted_shape": kind}


def tool_writer_insert_text_frame(args):
    doc = _require_writer()
    frame = doc.createInstance("com.sun.star.text.TextFrame")
    size = _uno_struct("com.sun.star.awt.Size")
    size.Width = _mm100(args.get("width_mm", 50))
    size.Height = _mm100(args.get("height_mm", 30))
    frame.Size = size
    text, cursor = _writer_end_cursor(doc)
    text.insertTextContent(cursor, frame, False)
    if args.get("text"):
        ftext = frame.getText()
        ftext.insertString(ftext.createTextCursor(), args["text"], False)
    if args.get("name"):
        try:
            frame.Name = args["name"]
        except Exception:
            pass
    return {"inserted": "text_frame"}


def tool_writer_mail_merge(args):
    doc = _require_writer()
    url = doc.getURL()
    if not url:
        raise RuntimeError("Save the document first — mail merge needs a DocumentURL.")
    from com.sun.star.sdb.CommandType import TABLE, QUERY, COMMAND
    from com.sun.star.text.MailMergeType import FILE as MM_FILE, PRINTER, MAIL
    state = _connect()
    mm = state["smgr"].createInstanceWithContext(
        "com.sun.star.text.MailMerge", state["ctx"])
    mm.DocumentURL = url
    mm.DataSourceName = args["data_source"]
    mm.CommandType = {"table": TABLE, "query": QUERY, "command": COMMAND}.get(
        str(args.get("command_type", "table")).lower(), TABLE)
    mm.Command = args["command"]
    mm.OutputType = {"file": MM_FILE, "printer": PRINTER, "mail": MAIL}.get(
        str(args.get("output", "file")).lower(), MM_FILE)
    if args.get("output_url"):
        mm.OutputURL = _to_url(args["output_url"])
    mm.execute(())
    return {"merged": args["command"], "data_source": args["data_source"]}


def tool_writer_track_changes(args):
    doc = _require_writer()
    action = str(args.get("action", "status")).lower()
    if action == "enable":
        doc.setPropertyValue("RecordChanges", True)
    elif action == "disable":
        doc.setPropertyValue("RecordChanges", False)
    elif action == "accept_all":
        _dispatch(doc, ".uno:AcceptAllTrackedChanges")
    elif action == "reject_all":
        _dispatch(doc, ".uno:RejectAllTrackedChanges")
    elif action not in ("status", "list"):
        raise RuntimeError("action must be enable|disable|accept_all|reject_all|list|status.")
    redlines = []
    if action in ("status", "list"):
        try:
            enum = doc.getRedlines().createEnumeration()
            while enum.hasMoreElements():
                r = enum.nextElement()
                entry = {}
                for key, prop in (("author", "RedlineAuthor"),
                                  ("type", "RedlineType"),
                                  ("comment", "RedlineComment")):
                    try:
                        entry[key] = r.getPropertyValue(prop)
                    except Exception:
                        pass
                redlines.append(entry)
        except Exception:
            pass
    return {"recording": bool(doc.getPropertyValue("RecordChanges")),
            "redlines": redlines}


def tool_writer_insert_horizontal_rule(_args):
    doc = _require_writer()
    _append_paragraph(doc, style="Horizontal Line")
    return {"inserted": "horizontal_rule"}


def tool_writer_redact(args):
    doc = _require_writer()
    desc = doc.createSearchDescriptor()
    desc.SearchString = args["search"]
    desc.setPropertyValue("SearchCaseSensitive", bool(args.get("match_case", False)))
    found = doc.findAll(desc)
    n = found.getCount()
    for i in range(n):
        rng = found.getByIndex(i)
        rng.CharColor = 0x000000
        try:
            rng.CharHighlight = 0x000000
        except Exception:
            pass
        try:
            rng.CharBackColor = 0x000000
        except Exception:
            pass
    return {"redacted_matches": n,
            "note": "visual redaction (black-on-black) — not a secure content removal"}


def tool_writer_set_page_background(args):
    doc = _require_writer()
    styles = doc.getStyleFamilies().getByName("PageStyles")
    name = args.get("page_style") or "Standard"
    ps = styles.getByName(name) if styles.hasByName(name) else styles.getByIndex(0)
    if args.get("clear"):
        ps.BackTransparent = True
    elif args.get("color"):
        ps.BackColor = _hex_color(args["color"])
        ps.BackTransparent = False
    else:
        raise RuntimeError("Provide 'color' or set 'clear': true.")
    return {"page_style": ps.Name,
            "background": None if args.get("clear") else args.get("color")}


def tool_writer_set_watermark(args):
    doc = _require_writer()
    text = args.get("text", "")
    wm = [_pv("Text", text),
          _pv("Font", args.get("font", "Liberation Sans")),
          _pv("Angle", int(args.get("angle", 45))),
          _pv("Transparency", int(args.get("transparency", 50))),
          _pv("Color", _hex_color(args.get("color", "#c0c0c0")))]
    _dispatch(doc, ".uno:Watermark", wm)
    return {"watermark": text or "(cleared)"}


def tool_writer_spellcheck(args):
    import re
    from com.sun.star.lang import Locale
    doc = _require_writer()
    state = _connect()
    speller = state["smgr"].createInstanceWithContext(
        "com.sun.star.linguistic2.SpellChecker", state["ctx"])
    lang = str(args.get("language", "en-US")).replace("_", "-").split("-")
    loc = Locale()
    loc.Language = lang[0]
    loc.Country = lang[1] if len(lang) > 1 else ""
    limit = int(args.get("max_words", 100))
    seen = set()
    flagged = []
    for m in re.finditer(r"[^\W\d_]+", doc.getText().getString(), re.UNICODE):
        w = m.group(0)
        if w in seen:
            continue
        seen.add(w)
        try:
            if speller.isValid(w, loc, ()):
                continue
        except Exception:
            continue
        entry = {"word": w}
        try:
            res = speller.spell(w, loc, ())
            if res is not None:
                entry["suggestions"] = list(res.getAlternatives())[:5]
        except Exception:
            pass
        flagged.append(entry)
        if len(flagged) >= limit:
            break
    return {"flagged": flagged, "count": len(flagged)}


# --------------------------------------------------------------------------- #
# Tools — Calc P1/P2/P3 — see docs/TOOLS-WANTED.md
# --------------------------------------------------------------------------- #

_CALC_SHAPES = dict(_DRAW_SHAPES)


def _find_shape(sheet, name):
    dp = sheet.DrawPage
    for i in range(dp.getCount()):
        shp = dp.getByIndex(i)
        if getattr(shp, "Name", None) == name:
            return shp
    return None


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


def tool_calc_apply_cell_style(args):
    doc = _require_calc()
    sheet = _resolve_sheet(doc, args.get("sheet"))
    rng = sheet.getCellRangeByName(args["range"])
    if args.get("style"):
        rng.CellStyle = args["style"]
        return {"applied_style": args["style"], "range": args["range"]}
    return {"cell_style": rng.getCellByPosition(0, 0).CellStyle,
            "range": args["range"]}


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


# --------------------------------------------------------------------------- #
# Menu-coverage tools — Table / Format / Style / Form / Tools
# --------------------------------------------------------------------------- #

def _resolve_table(doc, args):
    tables = doc.getTextTables()
    name = args.get("name")
    if name not in (None, ""):
        if not tables.hasByName(name):
            raise RuntimeError("No table named %r. Tables: %s"
                               % (name, ", ".join(tables.getElementNames())))
        return tables.getByName(name)
    if tables.getCount() == 0:
        raise RuntimeError("The document has no tables.")
    return tables.getByIndex(int(args.get("index", 0)))


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


def _transform_case(s, mode):
    if mode == "upper":
        return s.upper()
    if mode == "lower":
        return s.lower()
    if mode == "title":
        return s.title()
    if mode == "sentence":
        return s.capitalize()
    raise RuntimeError("mode must be upper|lower|title|sentence.")


def tool_writer_change_case(args):
    """Change letter case of matched text ('search') or a body-paragraph range
    (start/count, default: all). ponytail: setString flattens direct formatting
    inside the changed range — fine for a case pass on plain text."""
    doc = _require_writer()
    mode = str(args.get("mode", "upper")).lower()
    if args.get("search"):
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findAll(desc)
        for i in range(found.getCount()):
            rng = found.getByIndex(i)
            rng.setString(_transform_case(rng.getString(), mode))
        return {"mode": mode, "ranges_changed": found.getCount(), "scope": "search"}
    start = int(args.get("start", 0))
    cnt = args.get("count")
    n = 0
    text = doc.getText()
    for i, para in _writer_paragraphs(doc):
        if i < start:
            continue
        if cnt is not None and i >= start + int(cnt):
            break
        s = para.getString()
        if s:
            cur = text.createTextCursorByRange(para.getStart())
            cur.gotoEndOfParagraph(True)
            cur.setString(_transform_case(s, mode))
        n += 1
    return {"mode": mode, "paragraphs_changed": n, "scope": "range"}


def tool_writer_apply_style(args):
    """Apply a named paragraph style (by 'search' match or start/count index) or
    a named character style (by 'search' match) — Styles-menu 'apply'."""
    doc = _require_writer()
    style = args["style"]
    kind = str(args.get("kind", "paragraph")).lower()
    if kind not in ("paragraph", "character"):
        raise RuntimeError("kind must be 'paragraph' or 'character'.")
    fam = "ParagraphStyles" if kind == "paragraph" else "CharacterStyles"
    if not doc.getStyleFamilies().getByName(fam).hasByName(style):
        raise RuntimeError("No %s style named %r." % (kind, style))
    prop = "ParaStyleName" if kind == "paragraph" else "CharStyleName"
    if args.get("search"):
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findAll(desc)
        for i in range(found.getCount()):
            setattr(found.getByIndex(i), prop, style)
        return {"style": style, "kind": kind, "applied": found.getCount(),
                "scope": "search"}
    if kind == "character":
        raise RuntimeError("Character styles need a 'search' target.")
    start = int(args.get("start", 0))
    cnt = args.get("count")
    n = 0
    for i, para in _writer_paragraphs(doc):
        if i < start:
            continue
        if cnt is not None and i >= start + int(cnt):
            break
        para.ParaStyleName = style
        n += 1
    return {"style": style, "kind": kind, "applied": n, "scope": "range"}


def _form_controls(doc):
    """Yield (form_name, control_model) over every form control in the active
    document — the Writer draw page, or each Calc sheet's draw page."""
    ub = _bridge()
    pages = []
    if ub.is_calc(doc):
        sheets = doc.getSheets()
        for i in range(sheets.getCount()):
            pages.append(sheets.getByIndex(i).getDrawPage())
    else:
        pages.append(doc.getDrawPage())
    for dp in pages:
        try:
            forms = dp.getForms()
        except Exception:
            continue
        for fi in range(forms.getCount()):
            form = forms.getByIndex(fi)
            for ci in range(form.getCount()):
                yield form.Name, form.getByIndex(ci)


def _control_info(model):
    info = {"name": getattr(model, "Name", "")}
    try:
        comp = [s for s in model.getSupportedServiceNames() if ".component." in s]
        info["type"] = comp[0].rsplit(".", 1)[-1] if comp else ""
    except Exception:
        info["type"] = ""
    try:
        psi = model.getPropertySetInfo()
        for p in ("Label", "Text", "DefaultText", "State", "Enabled", "ReadOnly"):
            if psi.hasPropertyByName(p):
                info[p] = _jsonable(getattr(model, p))
    except Exception:
        pass
    return info


def tool_form_control(args):
    """List form controls (action 'list') or set an existing control's
    properties by name (action 'set'): label, value, state, enabled, read_only,
    items. Works on Writer and Calc form controls."""
    doc = _current_doc()
    action = str(args.get("action", "list")).lower()
    if action == "list":
        out = []
        for form_name, model in _form_controls(doc):
            entry = _control_info(model)
            entry["form"] = form_name
            out.append(entry)
        return {"controls": out, "count": len(out)}
    if action != "set":
        raise RuntimeError("action must be 'list' or 'set'.")
    name = args["name"]
    target = None
    for _, model in _form_controls(doc):
        if getattr(model, "Name", None) == name:
            target = model
            break
    if target is None:
        raise RuntimeError("No form control named %r." % name)
    psi = target.getPropertySetInfo()
    applied = []

    def _set(prop, value):
        if psi.hasPropertyByName(prop):
            setattr(target, prop, value)
            applied.append(prop)
            return True
        return False

    if "label" in args:
        _set("Label", str(args["label"]))
    if "value" in args:
        if not _set("DefaultText", str(args["value"])):
            _set("Text", str(args["value"]))
    if "state" in args:            # checkbox/radio: 0 off, 1 on, 2 tristate
        _set("DefaultState", int(args["state"]))
        _set("State", int(args["state"]))
    if "enabled" in args:
        _set("Enabled", bool(args["enabled"]))
    if "read_only" in args:
        _set("ReadOnly", bool(args["read_only"]))
    if args.get("items") is not None:
        _set("StringItemList", tuple(str(x) for x in args["items"]))
    if not applied:
        raise RuntimeError("Give at least one of: label, value, state, enabled, "
                           "read_only, items.")
    return {"name": name, "applied": applied}


def tool_writer_set_chapter_numbering(args):
    """Configure heading (chapter) numbering: bind the first N outline levels to
    a numbering scheme so Heading 1/2/3 auto-number as 1, 1.1, 1.1.1 (Tools >
    Heading Numbering)."""
    import uno
    doc = _require_writer()
    levels = int(args.get("levels", 3))
    if levels < 1 or levels > 10:
        raise RuntimeError("levels must be 1..10.")
    from com.sun.star.style.NumberingType import (
        ARABIC, ROMAN_UPPER, ROMAN_LOWER, CHARS_UPPER_LETTER,
        CHARS_LOWER_LETTER, NUMBER_NONE)
    types = {"arabic": ARABIC, "roman_upper": ROMAN_UPPER,
             "roman_lower": ROMAN_LOWER, "letter_upper": CHARS_UPPER_LETTER,
             "letter_lower": CHARS_LOWER_LETTER, "none": NUMBER_NONE}
    numbering = str(args.get("numbering", "arabic")).lower()
    if numbering not in types:
        raise RuntimeError("numbering must be one of %s." % sorted(types))
    ntype = types[numbering]
    separator = str(args.get("separator", "."))
    rules = doc.getChapterNumberingRules()
    # Mutate the level's EXISTING PropertyValue structs in place, then hand the
    # SAME sequence back via uno.invoke with an explicit []PropertyValue Any —
    # a plain tuple is marshalled as the wrong UNO type (IllegalArgumentException),
    # and rebuilding structs with _pv loses the types the rule needs.
    # ParentNumbering already defaults to 10 (full path 1 / 1.1 / 1.1.1).
    want = {"NumberingType": ntype, "Prefix": "", "Suffix": separator}
    for lvl in range(levels):
        rule = list(rules.getByIndex(lvl))
        for pv in rule:
            if pv.Name in want:
                pv.Value = want[pv.Name]
        uno.invoke(rules, "replaceByIndex",
                   (lvl, uno.Any("[]com.sun.star.beans.PropertyValue", tuple(rule))))
    return {"levels": levels, "numbering": numbering, "separator": separator}


def tool_writer_move_paragraphs(args):
    """Move a block of body paragraphs to a new index via .uno:MoveUp/MoveDown
    (which preserves each paragraph's content and formatting). 'to' is the
    destination index in the current paragraph numbering; the block lands before
    the paragraph currently there (to == paragraph count appends at the end)."""
    doc = _require_writer()
    start = int(args["start"])
    count = int(args.get("count", 1))
    to = int(args["to"])
    if count < 1:
        raise RuntimeError("count must be >= 1.")
    paras = [p for _, p in _writer_paragraphs(doc)]
    n = len(paras)
    if start < 0 or start >= n:
        raise RuntimeError("No body paragraph at index %d (document has %d)."
                           % (start, n))
    end = min(start + count, n)
    count = end - start
    if start <= to < end:
        return {"moved": 0, "note": "target index is inside the moved block; no-op"}
    if to < 0 or to > n:
        raise RuntimeError("target index %d out of range (0..%d)." % (to, n))
    vc = doc.getCurrentController().getViewCursor()
    vc.gotoRange(paras[start].getStart(), False)
    vc.gotoRange(paras[end - 1].getEnd(), True)
    if to < start:
        command, steps = ".uno:MoveUp", start - to
    else:
        command, steps = ".uno:MoveDown", to - end
    for _ in range(steps):
        _dispatch(doc, command)
    return {"moved": count, "from": start, "to": to,
            "command": command, "steps": steps}


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


def tool_writer_insert_caption(args):
    """Insert an auto-numbering caption ('Figure 1 — ...') as a new paragraph,
    backed by a per-category SetExpression sequence field so numbers increment
    across captions of the same category."""
    doc = _require_writer()
    from com.sun.star.text.SetVariableType import SEQUENCE
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    category = str(args.get("category", "Figure"))
    label = args.get("text", "")
    sep = args.get("separator", " — ")
    nt = {"arabic": 4, "roman_upper": 2, "roman_lower": 3,
          "letter_upper": 0, "letter_lower": 1}.get(
              str(args.get("numbering", "arabic")).lower(), 4)
    mname = "com.sun.star.text.FieldMaster.SetExpression." + category
    masters = doc.getTextFieldMasters()
    if masters.hasByName(mname):
        master = masters.getByName(mname)
    else:
        master = doc.createInstance("com.sun.star.text.FieldMaster.SetExpression")
        master.Name = category
    field = doc.createInstance("com.sun.star.text.TextField.SetExpression")
    field.NumberingType = nt
    field.SubType = SEQUENCE
    field.attachTextFieldMaster(master)
    text = doc.getText()
    if args.get("search"):
        rng = _writer_find_first(doc, args["search"], args.get("match_case", False))
        if rng is None:
            raise RuntimeError("Search text %r not found." % args["search"])
        cur = text.createTextCursorByRange(rng.getEnd())
        cur.gotoEndOfParagraph(False)
        text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
    else:
        cur = text.createTextCursorByRange(text.getEnd())
        if text.getString():
            text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
    text.insertString(cur, category + " ", False)
    text.insertTextContent(cur, field, False)
    if label:
        text.insertString(cur, sep + label, False)
    try:
        doc.getTextFields().refresh()
    except Exception:
        pass
    return {"category": category, "number": field.getPresentation(False),
            "text": label}


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


def tool_writer_clear_formatting(args):
    """Remove direct character/paragraph formatting (reset to the underlying
    style) from matched text ('search') or a body-paragraph range (start/count,
    default all)."""
    doc = _require_writer()
    text = doc.getText()
    if args.get("search"):
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findAll(desc)
        for i in range(found.getCount()):
            r = found.getByIndex(i)
            # Use each match's OWN text (body / header / footer / frame) — using
            # the body `text` object on a header/footer range throws
            # "End of content node doesn't have the proper start node".
            r.getText().createTextCursorByRange(r).setAllPropertiesToDefault()
        return {"cleared": found.getCount(), "scope": "search"}
    start = int(args.get("start", 0))
    cnt = args.get("count")
    n = 0
    for i, para in _writer_paragraphs(doc):
        if i < start:
            continue
        if cnt is not None and i >= start + int(cnt):
            break
        cur = text.createTextCursorByRange(para.getStart())
        cur.gotoEndOfParagraph(True)
        cur.setAllPropertiesToDefault()
        n += 1
    return {"cleared": n, "scope": "range"}


def tool_writer_set_line_numbering(args):
    """Turn document line numbering on/off and set its interval/options
    (Tools > Line Numbering)."""
    doc = _require_writer()
    lnp = doc.getLineNumberingProperties()
    lnp.IsOn = bool(args.get("enable", True))
    if args.get("interval") is not None:
        lnp.Interval = int(args["interval"])
    if args.get("count_empty_lines") is not None:
        lnp.CountEmptyLines = bool(args["count_empty_lines"])
    if args.get("distance_mm") is not None:
        lnp.Distance = _mm100(args["distance_mm"])
    return {"enabled": bool(lnp.IsOn), "interval": lnp.Interval}


def tool_set_active_document(args):
    """Focus a specific open document so subsequent reads/writes target it,
    selected by 'title' (substring), 'url' (substring), or 0-based 'index' over
    the open documents. The fix for focus-stealing silently redirecting writes."""
    target = _select_doc(args)
    if target is None:
        raise RuntimeError("Give one of: title, url, or index.")
    _activate(target)
    return {"active": _doc_info(target), "open_count": len(_open_docs())}


def tool_writer_replace_image(args):
    """Replace an existing image's graphic (new 'path') and/or resize it
    (width_mm/height_mm), by image 'name' — e.g. swap a logo without rebuilding."""
    doc = _require_writer()
    name = args["name"]
    graphics = doc.getGraphicObjects()
    if not graphics.hasByName(name):
        raise RuntimeError("No image named %r. Images: %s"
                           % (name, ", ".join(graphics.getElementNames())))
    img = graphics.getByName(name)
    changed = []
    if args.get("path"):
        import unohelper
        st = _connect()
        gp = st["smgr"].createInstanceWithContext(
            "com.sun.star.graphic.GraphicProvider", st["ctx"])
        url = unohelper.systemPathToFileUrl(os.path.abspath(args["path"]))
        img.Graphic = gp.queryGraphic((_pv("URL", url),))
        changed.append("graphic")
    if args.get("width_mm") is not None:
        img.Width = _mm100(args["width_mm"])
        changed.append("width")
    if args.get("height_mm") is not None:
        img.Height = _mm100(args["height_mm"])
        changed.append("height")
    if not changed:
        raise RuntimeError("Give a new 'path' and/or width_mm/height_mm.")
    return {"image": name, "changed": changed}


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


def tool_writer_find(args):
    """Locate text (does NOT change it): scans body paragraphs and returns, for
    each paragraph that contains 'search', its 0-based index, occurrence count, a
    snippet, and its paragraph style — so callers can then target it by index."""
    doc = _require_writer()
    search = args["search"]
    if not search:
        raise RuntimeError("Give a non-empty 'search'.")
    mc = bool(args.get("match_case", False))
    limit = int(args.get("limit", 100))
    needle = search if mc else search.lower()
    out = []
    for i, para in _writer_paragraphs(doc):
        s = para.getString()
        hay = s if mc else s.lower()
        pos = hay.find(needle)
        if pos == -1:
            continue
        a = max(0, pos - 20)
        b = min(len(s), pos + len(search) + 20)
        snippet = ("…" if a > 0 else "") + s[a:b] + ("…" if b < len(s) else "")
        out.append({"paragraph": i, "occurrences": hay.count(needle),
                    "snippet": snippet,
                    "style": para.getPropertyValue("ParaStyleName")})
        if len(out) >= limit:
            break
    return {"matches": out, "paragraphs_matched": len(out),
            "total_occurrences": sum(m["occurrences"] for m in out)}


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


def tool_writer_list_figures(_args):
    """List images/figures with name, size (mm), anchor type, and the text of the
    paragraph they anchor to (often the caption or surrounding context)."""
    doc = _require_writer()
    graphics = doc.getGraphicObjects()
    out = []
    for nm in graphics.getElementNames():
        g = graphics.getByName(nm)
        entry = {"name": nm}
        try:
            entry["size_mm"] = [round(g.Size.Width / 100.0, 1),
                                round(g.Size.Height / 100.0, 1)]
        except Exception:
            pass
        try:
            entry["anchor"] = _enum_value(g.AnchorType)
        except Exception:
            pass
        try:
            entry["context"] = g.getAnchor().getString()[:80]
        except Exception:
            pass
        out.append(entry)
    return {"figures": out, "count": len(out)}


def tool_writer_set_document_defaults(args):
    """Set the document's base typography by editing the 'Standard' paragraph
    style — font_name and/or font_size, applied to Western + Complex (RTL/CTL) +
    Asian scripts so an Arabic base font actually takes effect."""
    doc = _require_writer()
    std = doc.getStyleFamilies().getByName("ParagraphStyles").getByName("Standard")
    changed = []
    if args.get("font_name"):
        name = args["font_name"]
        std.CharFontName = name
        std.CharFontNameComplex = name
        std.CharFontNameAsian = name
        changed.append("font_name")
    if args.get("font_size") is not None:
        sz = float(args["font_size"])
        std.CharHeight = sz
        std.CharHeightComplex = sz
        std.CharHeightAsian = sz
        changed.append("font_size")
    if not changed:
        raise RuntimeError("Give font_name and/or font_size.")
    return {"style": "Standard", "changed": changed}


_TAB_ALIGN = {"left": "LEFT", "right": "RIGHT", "center": "CENTER",
              "decimal": "DECIMAL"}


def tool_writer_insert_tab_stops(args):
    """Set paragraph tab stops (positions in mm) on matched paragraphs ('search')
    or a body-paragraph range (start/count, default all) — for aligned columns /
    signature lines. align: left/right/center/decimal; optional 'fill' char."""
    doc = _require_writer()
    positions = args.get("positions_mm")
    if not positions:
        raise RuntimeError("Give 'positions_mm' — a list of tab-stop positions in mm.")
    align = str(args.get("align", "left")).lower()
    if align not in _TAB_ALIGN:
        raise RuntimeError("align must be one of %s." % sorted(_TAB_ALIGN))
    fill = args.get("fill")
    fillchar = ord(fill[0]) if fill else 32
    stops = []
    for p in positions:
        ts = _uno_struct("com.sun.star.style.TabStop")
        ts.Position = _mm100(p)
        ts.Alignment = _uno_enum("com.sun.star.style.TabAlign", _TAB_ALIGN[align])
        ts.FillChar = fillchar
        stops.append(ts)
    stops = tuple(stops)

    def _apply(para):
        para.ParaTabStops = stops

    n = 0
    if args.get("search"):
        desc = doc.createSearchDescriptor()
        desc.SearchString = args["search"]
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        found = doc.findAll(desc)
        for i in range(found.getCount()):
            _apply(found.getByIndex(i))
            n += 1
        return {"tab_stops": len(stops), "paragraphs": n, "scope": "search"}
    start = int(args.get("start", 0))
    cnt = args.get("count")
    for i, para in _writer_paragraphs(doc):
        if i < start:
            continue
        if cnt is not None and i >= start + int(cnt):
            break
        _apply(para)
        n += 1
    return {"tab_stops": len(stops), "paragraphs": n, "scope": "range"}


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


def tool_batch(args):
    """Run several tool calls in one round-trip. 'operations' is a list of
    {tool, args}; returns each result/error in order. stop_on_error (default
    true) halts on the first failure. Nesting 'batch' is rejected."""
    ops = args.get("operations") or []
    stop = bool(args.get("stop_on_error", True))
    results = []
    for op in ops:
        name = op.get("tool")
        a = op.get("args") or {}
        if name == "batch":
            results.append({"tool": name, "ok": False, "error": "batch cannot nest"})
            if stop:
                break
            continue
        fn = TOOLS.get(name)
        if fn is None:
            results.append({"tool": name, "ok": False, "error": "unknown tool"})
            if stop:
                break
            continue
        try:
            results.append({"tool": name, "ok": True, "result": fn(a)})
        except Exception as exc:  # surface, don't abort the whole batch silently
            results.append({"tool": name, "ok": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            if stop:
                break
    return {"results": results, "count": len(results),
            "ok": all(r["ok"] for r in results)}


TOOLS = {
    # status & selection
    "lo_status": tool_lo_status,
    "list_documents": tool_list_documents,
    "lo_screenshot": tool_lo_screenshot,
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
    # calc conditional formatting & comments
    "calc_add_conditional_format": tool_calc_add_conditional_format,
    "calc_clear_conditional_formats": tool_calc_clear_conditional_formats,
    "calc_add_comment": tool_calc_add_comment,
    "calc_get_comments": tool_calc_get_comments,
    "calc_set_borders": tool_calc_set_borders,
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
    # writer comments & conditional sections
    "writer_add_comment": tool_writer_add_comment,
    "writer_get_comments": tool_writer_get_comments,
    "writer_add_conditional_section": tool_writer_add_conditional_section,
    # writer paragraph / page / table styling
    "writer_format_paragraph": tool_writer_format_paragraph,
    "writer_set_page_style": tool_writer_set_page_style,
    "writer_set_header_footer": tool_writer_set_header_footer,
    "writer_format_table": tool_writer_format_table,
    # form controls (both Calc and Writer)
    "insert_form_control": tool_insert_form_control,
    # automation & inspection
    "reload_document": tool_reload_document,
    "run_macro": tool_run_macro,
    "calc_list_shapes": tool_calc_list_shapes,
    "calc_delete_shape": tool_calc_delete_shape,
    "calc_set_active_sheet": tool_calc_set_active_sheet,
    "calc_sheet_properties": tool_calc_sheet_properties,
    "calc_set_validation": tool_calc_set_validation,
    "basic_module": tool_basic_module,
    "inspect_ods": tool_inspect_ods,
    "uno_exec": tool_uno_exec,
    # good first tools (single-API wrappers)
    "calc_sort_range": tool_calc_sort_range,
    "calc_set_dimensions": tool_calc_set_dimensions,
    "calc_set_visibility": tool_calc_set_visibility,
    "calc_move_sheet": tool_calc_move_sheet,
    "calc_recalculate": tool_calc_recalculate,
    "calc_delete_comment": tool_calc_delete_comment,
    "calc_delete_chart": tool_calc_delete_chart,
    "writer_word_count": tool_writer_word_count,
    "writer_read_table": tool_writer_read_table,
    "writer_get_paragraphs": tool_writer_get_paragraphs,
    "get_document_properties": tool_get_document_properties,
    "set_document_modified": tool_set_document_modified,
    # writer P1
    "writer_list_objects": tool_writer_list_objects,
    "writer_set_paragraph_text": tool_writer_set_paragraph_text,
    "writer_set_text_direction": tool_writer_set_text_direction,
    "writer_delete_paragraphs": tool_writer_delete_paragraphs,
    "writer_insert_field": tool_writer_insert_field,
    "writer_insert_toc": tool_writer_insert_toc,
    "writer_update_indexes": tool_writer_update_indexes,
    "writer_apply_list": tool_writer_apply_list,
    # cross-cutting (Calc & Writer)
    "set_hyperlink": tool_set_hyperlink,
    "export_document": tool_export_document,
    "set_document_properties": tool_set_document_properties,
    "list_styles": tool_list_styles,
    "set_style": tool_set_style,
    "protect_document": tool_protect_document,
    "dispatch_uno": tool_dispatch_uno,
    "document_undo": tool_document_undo,
    "bind_document_event": tool_bind_document_event,
    "set_view_zoom": tool_set_view_zoom,
    "get_signatures": tool_get_signatures,
    "list_embedded_objects": tool_list_embedded_objects,
    "insert_ole_object": tool_insert_ole_object,
    # writer P2/P3
    "writer_delete_object": tool_writer_delete_object,
    "writer_edit_table": tool_writer_edit_table,
    "writer_set_image_layout": tool_writer_set_image_layout,
    "writer_add_section": tool_writer_add_section,
    "writer_bookmarks": tool_writer_bookmarks,
    "writer_insert_cross_reference": tool_writer_insert_cross_reference,
    "writer_insert_footnote": tool_writer_insert_footnote,
    "writer_insert_shape": tool_writer_insert_shape,
    "writer_insert_text_frame": tool_writer_insert_text_frame,
    "writer_mail_merge": tool_writer_mail_merge,
    "writer_track_changes": tool_writer_track_changes,
    "writer_insert_horizontal_rule": tool_writer_insert_horizontal_rule,
    "writer_redact": tool_writer_redact,
    "writer_set_page_background": tool_writer_set_page_background,
    "writer_set_watermark": tool_writer_set_watermark,
    "writer_spellcheck": tool_writer_spellcheck,
    # menu coverage — Table / Format / Style / Form / Tools
    "writer_sort_table": tool_writer_sort_table,
    "writer_change_case": tool_writer_change_case,
    "writer_apply_style": tool_writer_apply_style,
    "form_control": tool_form_control,
    "writer_set_chapter_numbering": tool_writer_set_chapter_numbering,
    "writer_move_paragraphs": tool_writer_move_paragraphs,
    "writer_convert_table": tool_writer_convert_table,
    "writer_insert_caption": tool_writer_insert_caption,
    "writer_table_formula": tool_writer_table_formula,
    "writer_split_cells": tool_writer_split_cells,
    "writer_clear_formatting": tool_writer_clear_formatting,
    "writer_set_line_numbering": tool_writer_set_line_numbering,
    "set_active_document": tool_set_active_document,
    "writer_replace_image": tool_writer_replace_image,
    "writer_repeat_heading_rows": tool_writer_repeat_heading_rows,
    # inspection / navigation / export / batch
    "writer_find": tool_writer_find,
    "writer_list_tables": tool_writer_list_tables,
    "writer_list_figures": tool_writer_list_figures,
    "writer_set_document_defaults": tool_writer_set_document_defaults,
    "writer_insert_tab_stops": tool_writer_insert_tab_stops,
    "calc_export_range": tool_calc_export_range,
    "batch": tool_batch,
    # calc P1/P2/P3
    "calc_add_shape": tool_calc_add_shape,
    "calc_insert_image": tool_calc_insert_image,
    "calc_position_shape": tool_calc_position_shape,
    "calc_autofilter": tool_calc_autofilter,
    "calc_edit_chart": tool_calc_edit_chart,
    "calc_list_charts": tool_calc_list_charts,
    "calc_named_ranges": tool_calc_named_ranges,
    "calc_create_pivot": tool_calc_create_pivot,
    "calc_refresh_pivot": tool_calc_refresh_pivot,
    "calc_add_subtotals": tool_calc_add_subtotals,
    "calc_goal_seek": tool_calc_goal_seek,
    "calc_fill_series": tool_calc_fill_series,
    "calc_cell_protection": tool_calc_cell_protection,
    "calc_format_cells_advanced": tool_calc_format_cells_advanced,
    "calc_get_cell_format": tool_calc_get_cell_format,
    "calc_get_conditional_formats": tool_calc_get_conditional_formats,
    "calc_get_validation": tool_calc_get_validation,
    "calc_page_setup": tool_calc_page_setup,
    "calc_set_print_area": tool_calc_set_print_area,
    "calc_standard_filter": tool_calc_standard_filter,
    "calc_group_shapes": tool_calc_group_shapes,
    "calc_group_outline": tool_calc_group_outline,
    "calc_multiple_operations": tool_calc_multiple_operations,
    "calc_remove_duplicates": tool_calc_remove_duplicates,
    "calc_transpose": tool_calc_transpose,
    "calc_apply_cell_style": tool_calc_apply_cell_style,
    "calc_add_sparkline": tool_calc_add_sparkline,
    "calc_add_scale_format": tool_calc_add_scale_format,
    "calc_copy_sheet": tool_calc_copy_sheet,
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
     "description": "Check the LibreOffice connection (reports the transport: pipe = agent-acceptor extension, socket = accept flag/auto-launch) and list open documents.",
     "inputSchema": _schema()},
    {"name": "list_documents",
     "description": "List the documents currently open in LibreOffice.",
     "inputSchema": _schema()},
    {"name": "lo_screenshot",
     "description": "Save a PNG screenshot of the LibreOffice WINDOW itself "
                    "(PrintWindow — captures the real GUI rendering even when "
                    "the window is behind others; PDF export can differ from "
                    "the screen, e.g. form controls on RTL sheets). "
                    "Windows-only. Returns the saved file path.",
     "inputSchema": _schema(
         {"path": dict(_STR, description="output .png path (default: temp dir)"),
          "window_title": dict(_STR, description="window-title substring to "
                               "match (default 'LibreOffice')")})},
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
     "description": "Close a document, optionally saving it first (save=true needs an existing file location). Targets a SPECIFIC doc by 'index'/'title'/'url' (recommended when several are open — focus alone can close the wrong one); defaults to the active document.",
     "inputSchema": _schema({"save": _BOOL,
                             "title": dict(_STR, description="match by window title substring"),
                             "url": dict(_STR, description="match by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
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
     "description": "Insert a table, optionally filled with data (rows of strings/numbers). By default appends at the document end; give 'search' to place it right after the first paragraph containing that text, or 'after_index' to place it after a 0-based body-paragraph index.",
     "inputSchema": _schema({"rows": _INT, "columns": _INT, "data": _GRID,
                             "search": dict(_STR, description="place the table after the paragraph containing this text"),
                             "after_index": dict(_INT, description="place the table after this 0-based body-paragraph index"),
                             "match_case": _BOOL}, ["rows", "columns"])},
    {"name": "writer_insert_image",
     "description": "Insert an image file at the end of the Writer document (size in mm; defaults to the image's own size).",
     "inputSchema": _schema({"path": _STR, "width_mm": _INT, "height_mm": _INT}, ["path"])},
    {"name": "writer_insert_page_break",
     "description": "Insert a page break at the end of the Writer document.",
     "inputSchema": _schema()},
    {"name": "writer_get_outline",
     "description": "List the document's headings/subheadings as an outline: [{level, text, index, style}, ...]. 'level' is the outline depth (1 = heading, 2 = subheading, 3 = sub-subheading, ...); 'index' is the body-paragraph index for targeting with writer_format_paragraph / writer_apply_style / writer_move_paragraphs.",
     "inputSchema": _schema()},
    # --- writer comments & conditional sections ---
    {"name": "writer_add_comment",
     "description": "Add a comment/annotation. Anchors to the first match of 'search' if given, else to the current selection, else at the document end.",
     "inputSchema": _schema({"text": _STR,
                             "search": dict(_STR, description="anchor the comment to the first occurrence of this text"),
                             "match_case": _BOOL,
                             "author": dict(_STR, description="comment author (default 'Claude')")},
                            ["text"])},
    {"name": "writer_get_comments",
     "description": "List the document's comments: [{author, text, anchor, resolved}].",
     "inputSchema": _schema()},
    {"name": "writer_add_conditional_section",
     "description": "Writer's analog of conditional formatting: append text wrapped in a named CONDITIONAL SECTION that is HIDDEN when 'condition' evaluates true (LibreOffice field syntax, e.g. '1==1', 'user_field==\"x\"'). The condition is evaluated by Writer's layout when the document is viewed/printed. Set visible=false to hide the section immediately regardless of condition.",
     "inputSchema": _schema({"name": dict(_STR, description="unique section name"),
                             "condition": dict(_STR, description="hide-when-true condition, e.g. '1==1'"),
                             "text": _STR, "visible": _BOOL},
                            ["name", "condition"])},
    # --- writer paragraph / page / table styling ---
    {"name": "writer_format_paragraph",
     "description": "Paragraph formatting for Writer. Targets body paragraphs by 0-based 'start'/'count' (the index space writer_get_paragraphs reports), else paragraphs matching 'search', else ALL body paragraphs. Set alignment, line spacing (percent, e.g. 150 = 1.5x), space above/below (mm), left/right/first-line indent (mm), and/or a named paragraph style (e.g. 'Quotations', 'Title') — e.g. restyle one heading by index with start + style_name.",
     "inputSchema": _schema({"search": dict(_STR, description="format paragraphs containing this text; omit for all"),
                             "start": dict(_INT, description="first paragraph index (0-based); overrides search"),
                             "count": dict(_INT, description="how many paragraphs from 'start' (default: to end)"),
                             "match_case": _BOOL,
                             "align": dict(_STR, enum=["left", "center", "right", "justify"]),
                             "line_spacing_percent": dict(_INT, description="e.g. 100, 150, 200"),
                             "space_above_mm": _NUM, "space_below_mm": _NUM,
                             "indent_left_mm": _NUM, "indent_right_mm": _NUM,
                             "first_line_indent_mm": _NUM,
                             "style_name": dict(_STR, description="named paragraph style to apply")})},
    {"name": "writer_set_page_style",
     "description": "Page styling for Writer: paper size (a4/a5/a3/letter/legal, or width_mm+height_mm), orientation (portrait/landscape), page margins (mm), and column count. Applies to the document's page style.",
     "inputSchema": _schema({"paper": dict(_STR, enum=["a4", "a5", "a3", "letter", "legal"]),
                             "width_mm": _NUM, "height_mm": _NUM,
                             "orientation": dict(_STR, enum=["portrait", "landscape"]),
                             "margin_top_mm": _NUM, "margin_bottom_mm": _NUM,
                             "margin_left_mm": _NUM, "margin_right_mm": _NUM,
                             "columns": dict(_INT, description="number of text columns"),
                             "style_name": dict(_STR, description="page style name (default: the one in use)")})},
    {"name": "writer_set_header_footer",
     "description": "Enable/disable and set the text of the Writer page header or footer.",
     "inputSchema": _schema({"which": dict(_STR, enum=["header", "footer"]),
                             "enable": dict(_BOOL, description="default true"),
                             "text": _STR,
                             "style_name": _STR})},
    {"name": "writer_format_table",
     "description": "Format a Writer table (by name or 0-based index): draw a full-grid border (width in pt + color) and/or style the header row (bold, background color, font color).",
     "inputSchema": _schema({"name": dict(_STR, description="table name; or use index"),
                             "index": dict(_INT, description="0-based table index (default 0)"),
                             "border_width_pt": _NUM,
                             "border_color": dict(_STR, description="'#RRGGBB'"),
                             "header_bold": _BOOL,
                             "header_background": dict(_STR, description="'#RRGGBB'"),
                             "header_font_color": dict(_STR, description="'#RRGGBB'")})},
    # --- form controls (buttons and other UI elements) ---
    {"name": "insert_form_control",
     "description": "Insert a form control (UI element) into the active Calc sheet or Writer document: a push button, checkbox, text field, label, or dropdown list box. Position and size in mm. For a button, 'url' makes it open a URL/dispatch command when clicked. For a listbox, 'items' are the dropdown entries.",
     "inputSchema": _schema({"kind": dict(_STR, enum=["button", "checkbox", "textfield", "label", "listbox"]),
                             "label": dict(_STR, description="caption (button/checkbox/label)"),
                             "text": dict(_STR, description="default text (textfield)"),
                             "items": {"type": "array", "items": _STR, "description": "dropdown entries (listbox)"},
                             "url": dict(_STR, description="button target URL / dispatch command"),
                             "name": dict(_STR, description="control name"),
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["kind"])},
    # --- automation & inspection ---
    {"name": "reload_document",
     "description": "Store, close and reload the active document from disk. THE verification step after shape/macro work: the in-memory model can lie (e.g. form-control shapes are silently dropped by the ODS writer on RTL sheets) — only a reload shows what actually serialized. Reloads with macros enabled.",
     "inputSchema": _schema({"save": dict(_BOOL, description="store before closing (default true)")})},
    {"name": "run_macro",
     "description": "Invoke a macro in the active document and return its result. 'name' is 'Library.Module.Sub' (document Basic), 'Module.Sub' (Standard library), or a full vnd.sun.star.script: URI.",
     "inputSchema": _schema({"name": dict(_STR, description="e.g. 'KahataynForms.Engine.RefreshView'"),
                             "args": {"type": "array", "description": "positional arguments"}},
                            ["name"])},
    {"name": "calc_list_shapes",
     "description": "List everything on a sheet's DrawPage: shape names, types, positions/sizes (mm), text, OnClick script, and whether each is a form control. Use to verify buttons/shapes really exist where you think they do.",
     "inputSchema": _schema({"sheet": _SHEET})},
    {"name": "calc_delete_shape",
     "description": "Delete shape(s) with the given name from a sheet's DrawPage.",
     "inputSchema": _schema({"name": dict(_STR, description="shape name"), "sheet": _SHEET}, ["name"])},
    {"name": "calc_set_active_sheet",
     "description": "Activate a sheet in the LibreOffice window and optionally select AND scroll to a cell (plain select() does not scroll the viewport).",
     "inputSchema": _schema({"sheet": _SHEET,
                             "cell": dict(_STR, description="cell to select+scroll to, e.g. 'A15'")})},
    {"name": "calc_sheet_properties",
     "description": "Read and optionally set per-sheet properties: rtl (right-to-left layout — set BEFORE placing shapes, coordinates mirror), visible (hide/show), freeze_rows/freeze_cols (frozen panes). Omitted properties are left unchanged; the reply reports the current state.",
     "inputSchema": _schema({"sheet": _SHEET, "rtl": _BOOL, "visible": _BOOL,
                             "freeze_rows": _INT, "freeze_cols": _INT})},
    {"name": "calc_set_validation",
     "description": "Cell validity for a range: 'list' shows a dropdown (blocking wrong entries unless blocking=false), 'hint' shows an on-select help message, 'clear' removes validation. List and hint can combine.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "list": {"type": "array", "items": _STR, "description": "dropdown entries"},
                             "blocking": dict(_BOOL, description="reject entries outside the list (default true)"),
                             "hint": dict(_STR, description="on-select help message"),
                             "hint_title": _STR, "error_title": _STR, "error_message": _STR,
                             "clear": dict(_BOOL, description="remove existing validation first")},
                            ["range"])},
    {"name": "basic_module",
     "description": "Manage the active document's embedded Basic: action 'list' (libraries + modules with sizes), 'get' (module source), 'set' (create/replace module source). After 'set', invoke a no-op Sub via run_macro as a compile check — one syntax error silently disables the whole module.",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "get", "set"]),
                             "library": _STR, "module": _STR,
                             "source": dict(_STR, description="full module source (for set)")})},
    {"name": "inspect_ods",
     "description": "Regex-search inside the SAVED file's zip entries (content.xml by default) — the ground truth of what serialized, independent of the in-memory model. Defaults to the active document's file.",
     "inputSchema": _schema({"pattern": dict(_STR, description="regular expression"),
                             "path": dict(_STR, description="ods/odt path (default: active document)"),
                             "entry": dict(_STR, description="zip entry (default content.xml)"),
                             "context": dict(_INT, description="chars of context per excerpt (default 120)"),
                             "max_matches": dict(_INT, description="max excerpts returned (default 10)")},
                            ["pattern"])},
    {"name": "uno_exec",
     "description": "Escape hatch: run a short Python snippet against the live UNO bridge. In scope: ctx, smgr, desktop, doc (active document), uno. Printed output is returned as 'stdout'; assign to a variable named `result` to return a JSON value. Use when no dedicated tool fits.",
     "inputSchema": _schema({"code": dict(_STR, description="Python source to exec")}, ["code"])},
    # --- good first tools (single-API wrappers) ---
    {"name": "writer_word_count",
     "description": "Document statistics for the active Writer doc: word, paragraph, character counts and page count.",
     "inputSchema": _schema()},
    {"name": "writer_read_table",
     "description": "Read an existing Writer table back as a 2-D grid of cell strings. Give 'name' (from writer_list_objects / find) or a 0-based 'index' (default 0).",
     "inputSchema": _schema({"name": dict(_STR, description="table name (e.g. 'Table1')"),
                             "index": dict(_INT, description="0-based table index if no name")})},
    {"name": "writer_get_paragraphs",
     "description": "List body paragraphs as [{index, text, style, is_heading}] so callers can target a paragraph by 0-based index or applied style instead of a unique search string. Index counts only body paragraphs (skips tables/frames).",
     "inputSchema": _schema()},
    {"name": "calc_sort_range",
     "description": "Sort a cell range by one or more key columns. 'keys' is a list of {column: 0-based offset within the range, descending?, case_sensitive?}. Set has_header to keep the first row in place.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "keys": {"type": "array", "items": {"type": "object"},
                                      "description": "e.g. [{\"column\":0},{\"column\":2,\"descending\":true}]"},
                             "has_header": dict(_BOOL, description="exclude a header row from the sort (default false)")},
                            ["range", "keys"])},
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
    {"name": "calc_recalculate",
     "description": "Force a recalculation after bulk formula writes: hard=true (default) recomputes everything, hard=false only dirty cells.",
     "inputSchema": _schema({"hard": dict(_BOOL, description="calculateAll (default true) vs calculate")})},
    {"name": "calc_delete_comment",
     "description": "Delete the cell comment/annotation on a cell (companion to calc_add_comment / calc_get_comments).",
     "inputSchema": _schema({"cell": dict(_STR, description="e.g. 'B2'"), "sheet": _SHEET}, ["cell"])},
    {"name": "calc_delete_chart",
     "description": "Remove an embedded chart from a sheet by name.",
     "inputSchema": _schema({"name": dict(_STR, description="chart name"), "sheet": _SHEET}, ["name"])},
    {"name": "get_document_properties",
     "description": "Read the active document's metadata: title/author/subject/keywords/description, created/modified dates + editor, statistics, and custom user-defined properties.",
     "inputSchema": _schema()},
    {"name": "set_document_modified",
     "description": "Read the dirty flag and optionally set it: modified=false marks the document saved, true forces it dirty. Returns the resulting state.",
     "inputSchema": _schema({"modified": dict(_BOOL, description="omit to just read; false=clear, true=force")})},
    # --- writer P1 ---
    {"name": "writer_list_objects",
     "description": "Enumerate objects in the active Writer doc — graphics, text frames, embedded/OLE objects, and draw shapes (rectangle/ellipse/line/text) — with name, type, anchor, and size (mm). Discovery companion to writer_read_table / writer_get_paragraphs.",
     "inputSchema": _schema()},
    {"name": "writer_set_paragraph_text",
     "description": "Replace the text of the body paragraph at a 0-based 'index' (the index space writer_get_paragraphs reports). Single paragraph — newlines are not turned into paragraph breaks.",
     "inputSchema": _schema({"index": _INT, "text": _STR}, ["index", "text"])},
    {"name": "writer_set_text_direction",
     "description": "Set text writing direction to 'rtl' (Arabic/Hebrew) or 'ltr'. Default flips the WHOLE document: every body paragraph, every table-cell paragraph (tables=false to skip), and the page style (page=false to skip). Give 'start'/'count' to flip only a body-paragraph range instead. Also sets paragraph alignment to match (align=false to keep alignment, e.g. a centered title).",
     "inputSchema": _schema({"direction": dict(_STR, enum=["rtl", "ltr"]),
                             "start": dict(_INT, description="range mode: first paragraph index (0-based)"),
                             "count": dict(_INT, description="range mode: how many paragraphs (default: to end)"),
                             "align": dict(_BOOL, description="also set alignment right/left to match (default true)"),
                             "tables": dict(_BOOL, description="whole-doc mode: also flip table cells (default true)"),
                             "page": dict(_BOOL, description="whole-doc mode: also set the page style direction (default true)"),
                             "style_name": dict(_STR, description="page style to set (default: the one in use)")},
                            ["direction"])},
    {"name": "writer_delete_paragraphs",
     "description": "Delete body paragraphs by 0-based index: 'count' paragraphs starting at 'start' (default 1), including their paragraph breaks. The index space is the one writer_get_paragraphs reports. Deleting every paragraph leaves one empty paragraph (Writer requires at least one).",
     "inputSchema": _schema({"start": _INT,
                             "count": dict(_INT, description="how many paragraphs to delete (default 1)")},
                            ["start"])},
    {"name": "writer_insert_field",
     "description": "Insert a dynamic field at the document end (or a new trailing paragraph): page_number, page_count, date, time, title, or author. Refresh later with writer_update_indexes.",
     "inputSchema": _schema({"field": dict(_STR, enum=["page_number", "page_count", "date", "time", "title", "author"]),
                             "fixed": dict(_BOOL, description="date/time: freeze the value (default false = updates)"),
                             "new_paragraph": dict(_BOOL, description="insert on a new trailing paragraph (default false = inline at end)")})},
    {"name": "writer_insert_toc",
     "description": "Insert a Table of Contents built from heading outline levels, at the document end or (at_start=true) the top. Populated immediately; re-run writer_update_indexes after adding headings.",
     "inputSchema": _schema({"title": dict(_STR, description="heading shown above the TOC"),
                             "levels": dict(_INT, description="outline levels to include (default all)"),
                             "at_start": dict(_BOOL, description="insert at the top of the document (default false = end)")})},
    {"name": "writer_update_indexes",
     "description": "Refresh ALL tables of contents/indexes and all dynamic fields (page numbers, dates, counts) so they stop being stale after programmatic edits.",
     "inputSchema": _schema()},
    {"name": "writer_apply_list",
     "description": "Turn body paragraphs into a bulleted (default) or numbered (ordered=true) list by attaching NumberingRules directly (works regardless of localized list-style names). Targets paragraphs from 'start' (0-based) for 'count' paragraphs; omit count to go to the end. Errors if the range matches no paragraph or none could be changed.",
     "inputSchema": _schema({"ordered": dict(_BOOL, description="numbered list (default false = bulleted)"),
                             "start": dict(_INT, description="first paragraph index (default 0)"),
                             "count": dict(_INT, description="how many paragraphs (default: to end)")})},
    # --- cross-cutting (Calc & Writer) ---
    {"name": "set_hyperlink",
     "description": "Attach a clickable hyperlink. Calc: give 'cell' — replaces it with a URL field. Writer: give 'search' — links every matching text range.",
     "inputSchema": _schema({"url": _STR,
                             "cell": dict(_STR, description="Calc cell, e.g. 'B2'"),
                             "search": dict(_STR, description="Writer text to link"),
                             "text": dict(_STR, description="Calc display text (default: cell text or URL)"),
                             "target": dict(_STR, description="Writer target frame, e.g. '_blank'"),
                             "sheet": _SHEET, "match_case": _BOOL},
                            ["url"])},
    {"name": "export_document",
     "description": "Store to a path with filter options. format 'pdf' (page_range, pdfa, quality 0-100, password) or 'csv' (delimiter, quote). Format defaults to the path extension.",
     "inputSchema": _schema({"path": _STR,
                             "format": dict(_STR, enum=["pdf", "csv"]),
                             "page_range": dict(_STR, description="PDF pages, e.g. '1-3'"),
                             "pdfa": dict(_BOOL, description="PDF/A-1 archival"),
                             "quality": dict(_INT, description="PDF image quality 0-100"),
                             "password": dict(_STR, description="PDF open password"),
                             "delimiter": dict(_STR, description="CSV field delimiter (default ',')"),
                             "quote": dict(_STR, description="CSV text delimiter (default '\"')")},
                            ["path"])},
    {"name": "set_document_properties",
     "description": "Set document metadata: title/author/subject/description, keywords (array), and 'custom' user-defined properties ({name: value}; value null removes).",
     "inputSchema": _schema({"title": _STR, "author": _STR, "subject": _STR,
                             "description": _STR,
                             "keywords": {"type": "array", "items": _STR},
                             "custom": {"type": "object", "description": "user-defined props"}})},
    {"name": "list_styles",
     "description": "List style names by family: 'paragraph', 'character', 'cell', 'page', 'frame', 'numbering', ... Omit 'family' for all families. in_use_only filters to styles actually applied.",
     "inputSchema": _schema({"family": dict(_STR, description="style family (omit for all)"),
                             "in_use_only": _BOOL})},
    {"name": "set_style",
     "description": "Create or modify a named style in a family (paragraph/character/cell/page/frame). Sets font/size/color/background, optional 'parent' (inherit-from) and 'follow_style' (next-paragraph style, e.g. a heading followed by body text). Reusable across cells/paragraphs.",
     "inputSchema": _schema({"family": _STR, "name": _STR, "parent": _STR,
                             "follow_style": dict(_STR, description="next-paragraph style name, e.g. 'Standard'"),
                             "bold": _BOOL, "italic": _BOOL,
                             "font_name": _STR, "font_size": _NUM,
                             "font_color": _STR, "background_color": _STR},
                            ["family", "name"])},
    {"name": "protect_document",
     "description": "Set/remove protection. Calc: a 'sheet' protects that sheet, else the workbook structure; optional 'password'. Writer: toggles IsProtected on all text sections. protect=false unprotects.",
     "inputSchema": _schema({"protect": dict(_BOOL, description="protect (default true) or unprotect"),
                             "password": _STR, "sheet": _SHEET})},
    {"name": "dispatch_uno",
     "description": "Execute an arbitrary .uno: command against the active frame (e.g. '.uno:Undo', '.uno:GoToCell', '.uno:InsertPagebreak') with optional named args. Escape hatch when no dedicated tool fits.",
     "inputSchema": _schema({"command": dict(_STR, description="e.g. '.uno:GoToCell'"),
                             "args": {"type": "object", "description": "named PropertyValue args"}},
                            ["command"])},
    {"name": "document_undo",
     "description": "Undo/redo/clear the active document's undo stack, or just query it (action 'status'). Returns whether undo/redo are possible and the next undo title.",
     "inputSchema": _schema({"action": dict(_STR, enum=["undo", "redo", "clear", "status"])})},
    {"name": "bind_document_event",
     "description": "Bind (or clear) a Basic/script macro to a document event such as OnSave, OnLoad, OnModifyChanged, OnPrint. Omit 'script' to clear the binding.",
     "inputSchema": _schema({"event": dict(_STR, description="e.g. 'OnSave'"),
                             "script": dict(_STR, description="vnd.sun.star.script: URI (omit to clear)")},
                            ["event"])},
    {"name": "set_view_zoom",
     "description": "Set the window zoom: 'percent' (a number) and/or 'type' (optimal/page_width/whole_page/percent/page_width_exact).",
     "inputSchema": _schema({"percent": _INT,
                             "type": dict(_STR, enum=["optimal", "page_width", "whole_page", "percent", "page_width_exact"])})},
    {"name": "get_signatures",
     "description": "Report digital-signature status of the saved document: whether it is signed, validity, signer, and signing date.",
     "inputSchema": _schema()},
    {"name": "list_embedded_objects",
     "description": "List embedded images and OLE objects with name, type, and size (mm). Writer: graphics + embedded objects. Calc: DrawPage graphic/OLE shapes across all sheets.",
     "inputSchema": _schema()},
    {"name": "insert_ole_object",
     "description": "Embed an OLE object. Give 'object' (math/calc/chart) or a raw 'clsid'. Writer: inserts at the end. Calc: adds to a sheet's DrawPage at the given size.",
     "inputSchema": _schema({"object": dict(_STR, enum=["math", "calc", "chart"]),
                             "clsid": dict(_STR, description="explicit component CLSID"),
                             "sheet": _SHEET, "width_mm": _NUM, "height_mm": _NUM})},
    # --- writer P2/P3 ---
    {"name": "writer_delete_object",
     "description": "Delete a graphic, text frame, embedded object, draw shape, or text section by name.",
     "inputSchema": _schema({"name": _STR}, ["name"])},
    {"name": "writer_edit_table",
     "description": "Edit an existing Writer table (by 'name' or 0-based 'index'): insert/delete rows/columns (at_row/at_column), merge a cell range ('A1:B2'), and set a cell's background color and/or text ('cell' + 'background_color'/'text') — editing a cell after insert.",
     "inputSchema": _schema({"name": _STR, "index": _INT,
                             "insert_rows": _INT, "delete_rows": _INT, "at_row": _INT,
                             "insert_columns": _INT, "delete_columns": _INT, "at_column": _INT,
                             "merge": dict(_STR, description="cell range to merge, e.g. 'A1:B2'"),
                             "cell": dict(_STR, description="cell for background/text, e.g. 'A1'"),
                             "background_color": _STR,
                             "text": dict(_STR, description="replace the 'cell' text")})},
    {"name": "writer_set_image_layout",
     "description": "Set anchor (as_char/char/paragraph/page/frame), text wrap (none/through/parallel/dynamic/left/right), and absolute position (x_mm/y_mm) of an existing image or text frame by name.",
     "inputSchema": _schema({"name": _STR,
                             "anchor": dict(_STR, enum=["as_char", "char", "paragraph", "page", "frame"]),
                             "wrap": dict(_STR, enum=["none", "through", "parallel", "dynamic", "left", "right"]),
                             "x_mm": _NUM, "y_mm": _NUM},
                            ["name"])},
    {"name": "writer_add_section",
     "description": "Insert a named text section at the end, optionally multi-column and/or write-protected, wrapping optional text.",
     "inputSchema": _schema({"name": _STR, "text": _STR,
                             "columns": dict(_INT, description="number of columns"),
                             "protected": _BOOL},
                            ["name"])},
    {"name": "writer_bookmarks",
     "description": "Bookmark lifecycle: action 'list', 'insert' (at a 'search' match or the end), 'delete', 'get' (anchored text), or 'set' (replace anchored text).",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "insert", "delete", "get", "set"]),
                             "name": _STR, "search": _STR, "text": _STR, "match_case": _BOOL})},
    {"name": "writer_insert_cross_reference",
     "description": "Insert a cross-reference field at the end pointing at a bookmark or reference mark ('target'), showing its page/number/text ('show'). Refreshed on insert.",
     "inputSchema": _schema({"target": dict(_STR, description="bookmark / reference-mark name"),
                             "source": dict(_STR, enum=["bookmark", "reference_mark"]),
                             "show": dict(_STR, enum=["page", "number", "text"])},
                            ["target"])},
    {"name": "writer_insert_footnote",
     "description": "Insert a footnote or endnote (kind) with body text, anchored at a 'search' match or the document end.",
     "inputSchema": _schema({"kind": dict(_STR, enum=["footnote", "endnote"]),
                             "text": dict(_STR, description="note body text"),
                             "search": dict(_STR, description="anchor at this text (default: end)"),
                             "match_case": _BOOL})},
    {"name": "writer_insert_shape",
     "description": "Draw a rectangle/ellipse/line/text shape on the draw page at position/size (mm) with optional fill/line color, caption text, and name.",
     "inputSchema": _schema({"kind": dict(_STR, enum=["rectangle", "ellipse", "line", "text"]),
                             "x_mm": _NUM, "y_mm": _NUM, "width_mm": _NUM, "height_mm": _NUM,
                             "fill_color": _STR, "line_color": _STR, "text": _STR, "name": _STR})},
    {"name": "writer_insert_text_frame",
     "description": "Insert a floating text frame (text box) at the end with a given size (mm), optionally pre-filled with text and named.",
     "inputSchema": _schema({"width_mm": _NUM, "height_mm": _NUM, "text": _STR, "name": _STR})},
    {"name": "writer_mail_merge",
     "description": "Run a mail merge over Database fields already in the (saved) document, from a registered 'data_source' + 'command' (table/query name), emitting file/printer/mail output. Requires a registered data source.",
     "inputSchema": _schema({"data_source": dict(_STR, description="registered data source name"),
                             "command": dict(_STR, description="table or query name"),
                             "command_type": dict(_STR, enum=["table", "query", "command"]),
                             "output": dict(_STR, enum=["file", "printer", "mail"]),
                             "output_url": dict(_STR, description="output folder path (file output)")},
                            ["data_source", "command"])},
    {"name": "writer_track_changes",
     "description": "Manage tracked changes: action enable/disable recording, accept_all, reject_all, or list/status (returns recording state + pending redlines with author/type/comment).",
     "inputSchema": _schema({"action": dict(_STR, enum=["enable", "disable", "accept_all", "reject_all", "list", "status"])})},
    {"name": "writer_insert_horizontal_rule",
     "description": "Insert a horizontal divider line at the document end (a paragraph in the 'Horizontal Line' style).",
     "inputSchema": _schema()},
    {"name": "writer_redact",
     "description": "Black out every occurrence of a search term (black text on black background). NOTE: visual redaction only — the underlying text still exists in the file.",
     "inputSchema": _schema({"search": _STR, "match_case": _BOOL}, ["search"])},
    {"name": "writer_set_page_background",
     "description": "Set (color) or clear (clear=true) the page background color on a page style (default 'Standard').",
     "inputSchema": _schema({"color": dict(_STR, description="'#RRGGBB'"),
                             "clear": _BOOL, "page_style": _STR})},
    {"name": "writer_set_watermark",
     "description": "Add a text watermark (empty text clears it) with font, angle, transparency (0-100) and color across all pages.",
     "inputSchema": _schema({"text": _STR, "font": _STR,
                             "angle": _INT, "transparency": _INT, "color": _STR})},
    {"name": "writer_spellcheck",
     "description": "Spell-check the document body and return flagged words with suggestions. 'language' is a BCP-47 tag (default 'en-US'); 'max_words' caps results.",
     "inputSchema": _schema({"language": _STR, "max_words": _INT})},
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
    {"name": "calc_autofilter",
     "description": "Turn the AutoFilter dropdowns on for a range (enable=true, default) or off (enable=false).",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "enable": _BOOL,
                             "name": dict(_STR, description="database-range name (optional)")})},
    {"name": "calc_edit_chart",
     "description": "Modify an existing chart: title, subtitle, legend on/off, x/y axis titles, and chart_type (column/bar/line/area/pie/...).",
     "inputSchema": _schema({"name": _STR, "sheet": _SHEET, "title": _STR, "subtitle": _STR,
                             "legend": _BOOL, "x_axis_title": _STR, "y_axis_title": _STR,
                             "chart_type": _STR}, ["name"])},
    {"name": "calc_list_charts",
     "description": "List embedded charts on a sheet with name, source ranges, and header flags.",
     "inputSchema": _schema({"sheet": _SHEET})},
    {"name": "calc_named_ranges",
     "description": "Workbook named ranges: action 'list', 'add' (name + content like 'Sheet1.$A$1:$B$5'), or 'delete'.",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "add", "delete"]),
                             "name": _STR, "content": dict(_STR, description="the range reference"),
                             "sheet": _SHEET})},
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
    {"name": "calc_standard_filter",
     "description": "Apply a criteria filter that hides non-matching rows. 'conditions' is a list of {column: 0-based, operator: =|!=|>|>=|<|<=, value}.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "has_header": _BOOL,
                             "conditions": {"type": "array", "items": {"type": "object"}}},
                            ["range", "conditions"])},
    {"name": "calc_group_shapes",
     "description": "Group >=2 named shapes into one ('names' + optional 'group' name), or ungroup=true a group named 'group'.",
     "inputSchema": _schema({"sheet": _SHEET,
                             "names": {"type": "array", "items": _STR},
                             "group": _STR, "ungroup": _BOOL})},
    {"name": "calc_group_outline",
     "description": "Row/column outline: action group/ungroup/show/hide over a range (axis rows|columns), or clear the whole outline.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "action": dict(_STR, enum=["group", "ungroup", "show", "hide", "clear"]),
                             "axis": dict(_STR, enum=["rows", "columns"])})},
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
    {"name": "calc_apply_cell_style",
     "description": "Apply a named cell style (e.g. 'Good', 'Heading 1') to a range, or read the current style if 'style' is omitted.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET, "style": _STR}, ["range"])},
    {"name": "calc_add_sparkline",
     "description": "Add in-cell sparklines driven by a data range (LibreOffice 7.5+).",
     "inputSchema": _schema({"target_range": _RANGE, "data_range": _RANGE, "sheet": _SHEET},
                            ["target_range", "data_range"])},
    {"name": "calc_add_scale_format",
     "description": "Add a color-scale or data-bar conditional format to a range (kind colorscale|databar), with default thresholds/colors.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "kind": dict(_STR, enum=["colorscale", "databar"])}, ["range"])},
    {"name": "calc_copy_sheet",
     "description": "Duplicate a sheet within the document to 'new_name' at an optional 0-based position.",
     "inputSchema": _schema({"name": _STR, "new_name": _STR, "position": _INT},
                            ["name", "new_name"])},
    # --- menu coverage: Table / Format / Style / Form / Tools ---
    {"name": "writer_sort_table",
     "description": "Sort a Writer table's data rows by one key column (0-based 'key_column'), ascending or 'descending'. 'has_header' (default true) keeps row 0 pinned. Numeric-aware. Target by 'name' or 0-based 'index'.",
     "inputSchema": _schema({"name": _STR, "index": _INT,
                             "key_column": dict(_INT, description="0-based column to sort on (default 0)"),
                             "descending": _BOOL, "has_header": _BOOL})},
    {"name": "writer_change_case",
     "description": "Change letter case: mode upper/lower/title/sentence. Targets text matching 'search', else a body-paragraph range ('start'/'count', default all). Case only — no effect on Arabic.",
     "inputSchema": _schema({"mode": dict(_STR, enum=["upper", "lower", "title", "sentence"]),
                             "search": dict(_STR, description="change matched text; omit for paragraph range"),
                             "match_case": _BOOL,
                             "start": dict(_INT, description="first paragraph index (0-based)"),
                             "count": dict(_INT, description="how many paragraphs (default: to end)")},
                            ["mode"])},
    {"name": "writer_apply_style",
     "description": "Apply a named style to text. kind 'paragraph' (default): target a 'search' match or a start/count paragraph range. kind 'character': requires 'search'. The style must already exist (create it with set_style).",
     "inputSchema": _schema({"style": _STR,
                             "kind": dict(_STR, enum=["paragraph", "character"]),
                             "search": dict(_STR, description="apply to matches; paragraph kind may use start/count instead"),
                             "match_case": _BOOL, "start": _INT, "count": _INT},
                            ["style"])},
    {"name": "form_control",
     "description": "Manage existing form controls (Writer or Calc). action 'list' returns each control's form/name/type/props; action 'set' updates a control by 'name': label, value, state (0/1/2), enabled, read_only, items (listbox).",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "set"]),
                             "name": dict(_STR, description="control name (set)"),
                             "label": _STR, "value": _STR, "state": _INT,
                             "enabled": _BOOL, "read_only": _BOOL,
                             "items": {"type": "array", "items": _STR}})},
    {"name": "writer_set_chapter_numbering",
     "description": "Turn on heading (chapter) numbering: bind the first 'levels' outline levels (default 3) to a scheme so Heading 1/2/3 auto-number as 1, 1.1, 1.1.1. numbering arabic/roman_upper/roman_lower/letter_upper/letter_lower/none; 'separator' between/after numbers (default '.').",
     "inputSchema": _schema({"levels": dict(_INT, description="how many outline levels to number (default 3)"),
                             "numbering": dict(_STR, enum=["arabic", "roman_upper", "roman_lower", "letter_upper", "letter_lower", "none"]),
                             "separator": dict(_STR, description="separator/suffix, default '.'")})},
    {"name": "writer_move_paragraphs",
     "description": "Reorder body paragraphs: move the block of 'count' (default 1) paragraphs starting at 0-based 'start' to index 'to' (the block lands before the paragraph currently there; to == paragraph count appends at the end). Preserves content and formatting. Indices are the writer_get_paragraphs space.",
     "inputSchema": _schema({"start": _INT,
                             "count": dict(_INT, description="how many paragraphs to move (default 1)"),
                             "to": dict(_INT, description="destination index (0-based)")},
                            ["start", "to"])},
    {"name": "writer_convert_table",
     "description": "Convert between a table and text. direction 'to_text': turn a table (by 'name' or 0-based 'index') into rows of paragraphs, cells joined by 'separator' (default tab). direction 'to_table': turn body paragraphs [start, start+count) into a table, splitting each on 'separator' (default tab) into columns.",
     "inputSchema": _schema({"direction": dict(_STR, enum=["to_text", "to_table"]),
                             "name": _STR, "index": _INT,
                             "start": dict(_INT, description="to_table: first paragraph index (0-based)"),
                             "count": dict(_INT, description="to_table: how many paragraphs (default 1)"),
                             "separator": dict(_STR, description="cell delimiter (default tab)")},
                            ["direction"])},
    {"name": "writer_insert_caption",
     "description": "Insert an auto-numbering caption on a new paragraph, e.g. 'Figure 1 — Site plan'. 'category' names the number sequence (Figure/Table/... ; numbers increment across captions sharing a category). 'text' is the label, 'separator' joins number and label (default ' — '), 'numbering' the number style. With 'search', the caption is placed after the matched paragraph.",
     "inputSchema": _schema({"category": dict(_STR, description="sequence name, e.g. 'Figure' or 'Table'"),
                             "text": dict(_STR, description="caption label"),
                             "separator": dict(_STR, description="between number and label (default ' — ')"),
                             "numbering": dict(_STR, enum=["arabic", "roman_upper", "roman_lower", "letter_upper", "letter_lower"]),
                             "search": dict(_STR, description="place caption after this text's paragraph"),
                             "match_case": _BOOL})},
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
    {"name": "writer_clear_formatting",
     "description": "Remove direct character/paragraph formatting (reset to the underlying style) from text matching 'search', or a body-paragraph range ('start'/'count', default all).",
     "inputSchema": _schema({"search": dict(_STR, description="clear matched text; omit for paragraph range"),
                             "match_case": _BOOL,
                             "start": dict(_INT, description="first paragraph index (0-based)"),
                             "count": dict(_INT, description="how many paragraphs (default: to end)")})},
    {"name": "writer_set_line_numbering",
     "description": "Turn document line numbering on ('enable', default true) or off, and set 'interval' (number every Nth line), 'count_empty_lines', and left 'distance_mm' (Tools > Line Numbering).",
     "inputSchema": _schema({"enable": _BOOL,
                             "interval": dict(_INT, description="number every Nth line"),
                             "count_empty_lines": _BOOL,
                             "distance_mm": _NUM})},
    {"name": "set_active_document",
     "description": "Focus a specific open document so subsequent reads/writes target it — select by 'title' (substring, case-insensitive), 'url' (substring), or 0-based 'index' over the open docs (see list_documents). Fixes focus-stealing that silently redirects writes to the wrong document.",
     "inputSchema": _schema({"title": dict(_STR, description="match by window title substring"),
                             "url": dict(_STR, description="match by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
    {"name": "writer_replace_image",
     "description": "Replace an existing image by 'name': swap its graphic (new 'path') and/or resize it (width_mm/height_mm) in place — e.g. update a logo without rebuilding. Use writer_list_objects to find image names.",
     "inputSchema": _schema({"name": _STR,
                             "path": dict(_STR, description="new image file (omit to only resize)"),
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["name"])},
    {"name": "writer_repeat_heading_rows",
     "description": "Make a table's first 'rows' (default 1) repeat as a header on every page the table spans, or turn it off with repeat=false. Target the table by 'name' or 0-based 'index'.",
     "inputSchema": _schema({"name": _STR, "index": _INT,
                             "rows": dict(_INT, description="how many header rows (default 1)"),
                             "repeat": dict(_BOOL, description="on (default) or off")})},
    {"name": "writer_find",
     "description": "Locate text WITHOUT changing it: returns each matching body paragraph's 0-based index, occurrence count, a snippet, and its style — so you can then target it by index (writer_set_paragraph_text, writer_format_paragraph, writer_delete_paragraphs, ...). Read-only companion to writer_find_replace.",
     "inputSchema": _schema({"search": _STR, "match_case": _BOOL,
                             "limit": dict(_INT, description="max matching paragraphs (default 100)")},
                            ["search"])},
    {"name": "writer_list_tables",
     "description": "List every table with 0-based index, name, row/column counts, and a header-row preview — discovery for writer_edit_table / writer_sort_table / writer_convert_table / writer_table_formula.",
     "inputSchema": _schema()},
    {"name": "writer_list_figures",
     "description": "List images/figures with name, size (mm), anchor type, and the anchoring paragraph's text (often the caption/context) — discovery for writer_replace_image / writer_set_image_layout.",
     "inputSchema": _schema()},
    {"name": "writer_set_document_defaults",
     "description": "Set the document's base typography via the 'Standard' paragraph style: font_name and/or font_size, applied to Western + Complex (RTL/CTL) + Asian scripts so an Arabic base font actually takes effect document-wide.",
     "inputSchema": _schema({"font_name": _STR, "font_size": _NUM})},
    {"name": "writer_insert_tab_stops",
     "description": "Set paragraph tab stops (positions_mm = list of mm) on matched paragraphs ('search') or a body-paragraph range (start/count, default all). align left/right/center/decimal; optional 'fill' char (e.g. '.' for dotted signature lines).",
     "inputSchema": _schema({"positions_mm": {"type": "array", "items": _NUM,
                                              "description": "tab-stop positions in mm"},
                             "align": dict(_STR, enum=["left", "right", "center", "decimal"]),
                             "fill": dict(_STR, description="fill character, e.g. '.'"),
                             "search": _STR, "match_case": _BOOL,
                             "start": _INT, "count": _INT},
                            ["positions_mm"])},
    {"name": "calc_export_range",
     "description": "Export a cell 'range' (or the sheet's used range if omitted) to a CSV or JSON file at 'path'. format defaults to the path extension; CSV is UTF-8-BOM with an optional 'delimiter'.",
     "inputSchema": _schema({"path": _STR, "range": _RANGE, "sheet": _SHEET,
                             "format": dict(_STR, enum=["csv", "json"]),
                             "delimiter": dict(_STR, description="CSV delimiter (default ',')")},
                            ["path"])},
    {"name": "batch",
     "description": "Run several tool calls in one round-trip. 'operations' is a list of {tool, args}; returns each result/error in order. stop_on_error (default true) halts on the first failure. Cuts latency on long multi-step document builds.",
     "inputSchema": _schema({"operations": {"type": "array",
                                            "items": {"type": "object"},
                                            "description": "list of {tool, args}"},
                             "stop_on_error": _BOOL},
                            ["operations"])},
]


# --------------------------------------------------------------------------- #
# JSON-RPC / MCP plumbing
# --------------------------------------------------------------------------- #

def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


_SCOPE_NAMES = {"writer": "Writer", "calc": "Calc", "lo": "LibreOffice"}

# Args worth showing the operator, in priority order (what the action targets).
_SUMMARY_ARG_KEYS = ("action", "direction", "mode", "range", "cell", "sheet",
                     "name", "title", "index", "start", "count", "to", "search",
                     "language", "category", "style", "kind", "which", "field",
                     "command", "path", "positions_mm")
# Result fields worth showing (what the action produced/affected).
_SUMMARY_RESULT_KEYS = ("appended", "cells_filled", "paragraphs",
                        "paragraphs_matched", "table_cell_paragraphs", "deleted",
                        "moved", "rows_sorted", "cleared", "applied", "changed",
                        "rows", "columns", "count", "matches", "number",
                        "exported", "table", "created", "inserted", "enabled",
                        "header_rows", "scope", "page_style_set",
                        "connected", "transport", "direction")


def _summary_preview(value, limit=48):
    s = str(value).replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _action_summary(name, args, payload):
    """A one-line, human-readable narration of a tool call, so an operator
    watching Claude's CLI/Desktop understands what happened in the document
    without opening it. Purely derived from the tool name + salient args/result."""
    parts = name.split("_")
    scope = _SCOPE_NAMES.get(parts[0], "LibreOffice")
    verb = " ".join(parts[1:] if parts[0] in _SCOPE_NAMES else parts) or name
    arg_bits, res_bits = [], []
    if isinstance(args, dict):
        if args.get("text"):
            arg_bits.append("“%s”" % _summary_preview(args["text"]))
        for k in _SUMMARY_ARG_KEYS:
            v = args.get(k)
            if v not in (None, "") and not isinstance(v, dict):
                if isinstance(v, list):
                    v = "[%d]" % len(v)
                arg_bits.append("%s=%s" % (k, _summary_preview(v, 40)))
    if isinstance(payload, dict):
        for k in _SUMMARY_RESULT_KEYS:
            if k in payload:
                v = payload[k]
                if isinstance(v, list):
                    v = len(v)
                if not isinstance(v, (dict, list)):
                    res_bits.append("%s=%s" % (k, _summary_preview(v, 40)))
    line = "%s: %s" % (scope, verb)
    if arg_bits:
        line += "  ·  " + " ".join(arg_bits)
    if res_bits:
        line += "  →  " + " ".join(res_bits)
    return line


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
            payload = _call_with_reconnect(func, args)
            # Two blocks: a human-readable narration first (so an operator
            # watching Claude's CLI/Desktop sees WHAT was done in the document),
            # then the structured JSON (the model chains on content[-1]).
            summary = _action_summary(name, args, payload)
            text = json.dumps(payload, ensure_ascii=False)
            return _result(mid, {"content": [
                {"type": "text", "text": summary},
                {"type": "text", "text": text},
            ]})
        except Exception as exc:  # tool errors are reported in-band, not as JSON-RPC errors
            # UNO exceptions often have an EMPTY str() — always name the type
            msg = str(exc).strip() or "(no message)"
            return _result(mid, {"content": [{"type": "text",
                                              "text": "Error [%s]: %s" % (type(exc).__name__, msg)}],
                                 "isError": True})

    if mid is not None:
        return _error(mid, -32601, "Unknown method: %s" % method)
    return None  # unknown notification


def main():
    # Windows bundled Python defaults stdio to the locale codepage (cp1252),
    # which mangles Arabic/Unicode arguments on the way IN (bilingual sheet
    # names failed getByName). Force UTF-8 both ways.
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
