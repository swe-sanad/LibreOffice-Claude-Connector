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
SERVER_VERSION = "0.9.6"
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


# Tools that do not mutate document CONTENT — they get no undo context.
# Everything ELSE is wrapped, so one Ctrl+Z reverts a whole tool call instead of
# one cell of a 500-cell write. Lifecycle tools (open/save/close/convert) are
# listed here too: they are not in-document edits, and wrapping them would grab
# the undo manager of whichever document happened to be current beforehand.
_NO_UNDO = frozenset("""
lo_status lo_screenshot list_documents list_macros list_styles list_templates
list_embedded_objects get_current_selection get_document_properties get_signatures
read_spreadsheet inspect_ods calc_overview calc_detect_errors
list_recent_documents print_document lo_health lo_recover checkpoint_document
document_watch print_settings document_lifecycle
create_document open_document create_from_template close_document save_document
export_document reload_document set_active_document convert merge
dispatch batch document_undo
calc_read_range calc_get_cell_format calc_get_comments calc_get_conditional_formats
calc_get_formulas calc_get_used_range calc_get_validation calc_list_charts
calc_list_shapes calc_list_sheets calc_export_range calc_statistics
calc_select_range calc_set_active_sheet calc_recalculate
writer_get_comments writer_get_outline writer_get_paragraphs writer_get_text
writer_list_figures writer_list_objects writer_list_tables writer_read_table
writer_find writer_word_count
impress_overview impress_read_slide impress_export_slides impress_slideshow
draw_overview draw_read_page
set_view_zoom set_document_modified
""".split())


class ToolTimeout(RuntimeError):
    """A UNO call did not come back — almost always a modal dialog in the GUI."""


_DEFAULT_CALL_TIMEOUT = 120.0


def _call_timeout():
    """Seconds to wait for one tool call; LO_CALL_TIMEOUT overrides, 0 disables.

    Generous on purpose: a COLD auto-launch of LibreOffice on a fresh profile
    can genuinely take a minute or more. A dialog blocks forever, so a large
    value still catches the case this exists for.
    """
    raw = os.environ.get("LO_CALL_TIMEOUT", "").strip()
    try:
        seconds = float(raw) if raw else _DEFAULT_CALL_TIMEOUT
    except ValueError:
        seconds = _DEFAULT_CALL_TIMEOUT
    return seconds if seconds > 0 else 0.0


def _run_with_timeout(func, seconds):
    """Run `func` on a worker thread so a wedged UNO call cannot wedge us.

    A UNO call waiting on a modal LibreOffice dialog never returns and cannot be
    interrupted from Python — so the thread is abandoned (daemon, dies with the
    process) and the cached bridge is dropped, meaning the NEXT call opens a
    fresh connection instead of queueing behind the stuck one. Without this the
    server simply stops answering and the MCP client eventually kills it, which
    is the least debuggable failure this server has.
    """
    if not seconds:
        return func()
    import threading
    box = {}

    def runner():
        try:
            box["value"] = func()
        except BaseException as exc:          # relayed to the calling thread
            box["error"] = exc

    worker = threading.Thread(target=runner, name="lo-tool-call", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        _reset_connection()
        raise ToolTimeout(
            "LibreOffice did not answer within %gs. It is almost certainly "
            "showing a dialog that is waiting for a person — a Save, a document "
            "recovery prompt, or a 'file is locked' warning. Switch to the "
            "LibreOffice window, dismiss it, then retry. (Raise LO_CALL_TIMEOUT "
            "if this was simply a slow operation.)" % seconds)
    if "error" in box:
        raise box["error"]
    return box.get("value")


# (code, retryable, hint) chosen by the FIRST matching substring of our own
# error text. Substring matching is brittle in general — it is acceptable here
# only because these are strings this file raises itself; the UNO-originated
# cases are caught by exception type instead.
_ERROR_RULES = (
    ("no document is currently open", "no_document", False,
     "Open or create a document first — open_document, create_document, or "
     "list_recent_documents to find what the user meant."),
    ("documents are open but none is focused", "ambiguous_document", False,
     "Name the document: pass index, title or url (list_documents shows them)."),
    ("is not a calc spreadsheet", "wrong_doc_type", False,
     "This tool only works on a spreadsheet. Use list_documents to find or "
     "switch to one with set_active_document."),
    ("is not a writer document", "wrong_doc_type", False,
     "This tool only works on a text document. Use list_documents to find or "
     "switch to one with set_active_document."),
    ("no sheet matches", "not_found", False,
     "Call calc_list_sheets (or calc_overview) for the real sheet names."),
    ("no table named", "not_found", False, "Call writer_list_tables for the names."),
    ("no image named", "not_found", False, "Call writer_list_figures for the names."),
    ("no comment matched", "not_found", False, "Call writer_get_comments first."),
    ("no caption matched", "not_found", False,
     "Call writer_captions with action='list' first."),
    ("not found", "not_found", False, "Check the path or name and try again."),
    ("locked", "locked", False,
     "The file is open elsewhere, or a stale .~lock file was left by a crash. "
     "lo_health reports stale locks."),
    ("must be one of", "invalid_argument", False, "Use one of the listed values."),
    ("give '", "invalid_argument", False, "Supply the argument named in the message."),
)


def _classify_error(exc):
    """Turn an exception into {code, message, hint, retryable} so the caller can
    tell 'retry this' from 'ask the user' from 'you called the wrong tool'."""
    text = (str(exc) or "").strip()
    low = text.lower()
    kind = type(exc).__name__

    if isinstance(exc, ToolTimeout):
        code, retryable, hint = "timeout", True, (
            "Dismiss the dialog in LibreOffice, then call the tool again.")
    elif _is_connection_error(exc):
        code, retryable, hint = "office_unreachable", True, (
            "LibreOffice closed or restarted. Retrying reconnects, and "
            "relaunches it if nothing is running.")
    elif isinstance(exc, KeyError):
        code, retryable = "invalid_argument", False
        hint = "Required argument %s was not supplied." % text
        text = "missing required argument %s" % text
    else:
        code, retryable, hint = "uno_error", False, ""
        for needle, rule_code, rule_retry, rule_hint in _ERROR_RULES:
            if needle in low:
                code, retryable, hint = rule_code, rule_retry, rule_hint
                break

    return {"code": code, "error_type": kind, "retryable": retryable,
            "message": text or "(no message)", "hint": hint}


def _enter_undo(name):
    """Open an undo context so every edit a tool makes collapses into ONE
    Ctrl+Z. Best-effort: a read-only tool, no open document, or a model with no
    undo manager all just mean no grouping — never a failed tool call."""
    if not name or name in _NO_UNDO:
        return None
    try:
        mgr = _current_doc().getUndoManager()
        mgr.enterUndoContext("Claude: %s" % name)
        return mgr
    except Exception:
        return None


def _leave_undo(mgr):
    if mgr is not None:
        try:
            mgr.leaveUndoContext()   # an empty context is discarded by LO
        except Exception:
            pass


def _call_with_reconnect(func, args, name=None):
    """Run a tool inside a single undo context; if the UNO bridge was lost since
    we cached the connection (LibreOffice restarted), drop the stale connection
    and retry ONCE. This is what makes the server survive an office restart
    instead of returning 'Binary URP bridge already disposed' forever."""
    def _run():
        mgr = _enter_undo(name)
        try:
            result = func(args)
        finally:
            _leave_undo(mgr)
        if name and name not in _NO_UNDO:
            _note_our_edit()   # so document_watch can tell our edits from theirs
        return result

    try:
        return _run()
    except Exception as exc:
        if not _is_connection_error(exc):
            raise
        _log("UNO bridge lost (%s) - reconnecting and retrying once" % exc)
        _reset_connection()
        return _run()


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


def _require_impress():
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_impress(doc):
        raise RuntimeError("The active document is not a presentation (Impress).")
    return doc


def _require_draw():
    ub = _bridge()
    doc = _current_doc()
    if not ub.is_draw(doc):
        raise RuntimeError("The active document is not a drawing (Draw).")
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
            else "writer" if ub.is_writer(doc)
            else "impress" if ub.is_impress(doc)
            else "draw" if ub.is_draw(doc) else "other")


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
    advertised = len(_advertised_tools())
    out = {"connected": True,
           "transport": _state.get("transport"),
           "tools_advertised": advertised,
           "tools_total": len(TOOLS),
           "tool_tier": os.environ.get("LO_TOOLS", "basic").strip().lower(),
           "documents": [_doc_info(doc) for doc in _open_docs()]}
    if advertised < len(TOOLS):
        out["more_tools"] = ("%d further tools are available via dispatch "
                            "(use dispatch with tool='list' for the catalog); "
                            "set LO_TOOLS=full to advertise them all directly."
                            % (len(TOOLS) - advertised))
    if _state.get("transport") == "socket":
        tip = ("Connected over a socket, so Claude can only reach a LibreOffice "
               "it launched itself. Installing the agent-acceptor extension "
               "makes a LibreOffice you opened normally reachable too.")
        oxt = _bundled_oxt()
        if oxt:
            tip += (' It ships with this connector — install it once with:  '
                    'unopkg add --suppress-license -f "%s"  '
                    '(then restart LibreOffice).' % oxt)
        out["tip"] = tip
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
                 "writer": "private:factory/swriter",
                 "impress": "private:factory/simpress",
                 "draw": "private:factory/sdraw"}

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
    ("impress", "native"): "impress8",
    ("impress", "odp"): "impress8",
    ("impress", "pptx"): "Impress MS PowerPoint 2007 XML",
    ("impress", "pdf"): "impress_pdf_Export",
    ("draw", "native"): "draw8",
    ("draw", "odg"): "draw8",
    ("draw", "pdf"): "draw_pdf_Export",
    ("draw", "svg"): "draw_svg_Export",
    ("draw", "png"): "draw_png_Export",
}


def tool_create_document(args):
    kind = args.get("type", "calc")
    url = _FACTORY_URLS.get(kind)
    if url is None:
        raise RuntimeError("type must be one of %s, got: %r"
                           % (sorted(_FACTORY_URLS), kind))
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


# Character properties carried across a format-preserving replacement. Kept to
# the run-level ones a user would notice; paragraph properties are untouched
# because the replacement never leaves its paragraph.
_CHAR_PROPS = ("CharWeight", "CharPosture", "CharUnderline", "CharFontName",
               "CharHeight", "CharColor", "CharBackColor", "CharStrikeout",
               "CharEscapement", "CharWeightComplex", "CharPostureComplex",
               "CharFontNameComplex", "CharHeightComplex")


def _char_props_at(text, rng):
    """Snapshot the character formatting of the FIRST character of a range."""
    probe = text.createTextCursorByRange(rng.getStart())
    probe.goRight(1, True)
    snapshot = {}
    for name in _CHAR_PROPS:
        try:
            snapshot[name] = getattr(probe, name)
        except Exception:
            pass
    return snapshot


def tool_writer_find_replace(args):
    doc = _require_writer()
    regex = bool(args.get("regex", False))

    if not args.get("preserve_formatting", True):
        desc = doc.createReplaceDescriptor()
        desc.SearchString = args["search"]
        desc.ReplaceString = args.get("replace", "")
        desc.setPropertyValue("SearchCaseSensitive",
                              bool(args.get("match_case", False)))
        desc.setPropertyValue("SearchWords", bool(args.get("whole_words", False)))
        desc.setPropertyValue("SearchRegularExpression", regex)
        return {"replacements": doc.replaceAll(desc), "regex": regex,
                "preserved_formatting": False}

    # replaceAll keeps formatting when a match sits inside ONE formatting run,
    # but a match spanning runs comes back chopped along the OLD boundaries:
    # "plain <b>BOLD</b> tail" -> "REPLA" plain + "CEMENT" bold. So replace each
    # match by hand and stamp it with the formatting of its first character.
    text = doc.getText()
    desc = doc.createSearchDescriptor()
    desc.SearchString = args["search"]
    desc.setPropertyValue("SearchCaseSensitive",
                          bool(args.get("match_case", False)))
    desc.setPropertyValue("SearchWords", bool(args.get("whole_words", False)))
    desc.setPropertyValue("SearchRegularExpression", regex)
    found = doc.findAll(desc)

    replacement = args.get("replace", "")
    ranges = [found.getByIndex(i) for i in range(found.getCount())]
    count = 0
    # in reverse: replacing shortens/extends the text and would shift the
    # ranges that come after it
    for rng in reversed(ranges):
        try:
            host = rng.getText() or text
            props = _char_props_at(host, rng)
            rng.setString(replacement)
            for name, value in props.items():
                try:
                    setattr(rng, name, value)
                except Exception:
                    pass
            count += 1
        except Exception:
            continue
    return {"replacements": count, "regex": regex,
            "preserved_formatting": True}


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

# Everything under Writer's Form menu that instantiates without a database
# connection. ImageControl and GridControl (Table Control) are deliberately
# absent: both are data-bound and need a form/data source context.
_FORM_COMPONENTS = {
    "button": "com.sun.star.form.component.CommandButton",
    "imagebutton": "com.sun.star.form.component.ImageButton",
    "checkbox": "com.sun.star.form.component.CheckBox",
    "radio": "com.sun.star.form.component.RadioButton",
    "groupbox": "com.sun.star.form.component.GroupBox",
    "textfield": "com.sun.star.form.component.TextField",
    "label": "com.sun.star.form.component.FixedText",
    "listbox": "com.sun.star.form.component.ListBox",
    "combobox": "com.sun.star.form.component.ComboBox",
    "formatted": "com.sun.star.form.component.FormattedField",
    "date": "com.sun.star.form.component.DateField",
    "time": "com.sun.star.form.component.TimeField",
    "numeric": "com.sun.star.form.component.NumericField",
    "currency": "com.sun.star.form.component.CurrencyField",
    "pattern": "com.sun.star.form.component.PatternField",
    "file": "com.sun.star.form.component.FileControl",
    "scrollbar": "com.sun.star.form.component.ScrollBar",
    "spinbutton": "com.sun.star.form.component.SpinButton",
    "navbar": "com.sun.star.form.component.NavigationToolBar",
}

# kinds that carry a visible caption. NOT imagebutton — it shows a picture and
# has no Label property at all (setting one raises AttributeError).
_FORM_LABELLED = ("button", "checkbox", "radio", "groupbox", "label")
_FORM_LISTED = ("listbox", "combobox")
_FORM_DROPDOWN_DEFAULT = ("listbox", "combobox")


def tool_insert_form_control(args):
    ub = _bridge()
    doc = _current_doc()
    kind = str(args.get("kind", "button")).lower()
    service = _FORM_COMPONENTS.get(kind)
    if service is None:
        raise RuntimeError("kind must be one of %s" % sorted(_FORM_COMPONENTS))

    model = doc.createInstance(service)
    if kind in _FORM_LABELLED and "label" in args:
        model.Label = args["label"]
    if kind == "textfield" and "text" in args:
        model.DefaultText = args["text"]
    if kind in _FORM_LISTED and args.get("items"):
        model.StringItemList = tuple(str(x) for x in args["items"])
        if kind in _FORM_DROPDOWN_DEFAULT:
            model.Dropdown = True
    if kind in ("button", "imagebutton") and args.get("url"):
        model.ButtonType = _uno_enum("com.sun.star.form.FormButtonType", "URL")
        model.TargetURL = args["url"]
    if kind == "imagebutton" and args.get("image"):
        model.ImageURL = _to_url(args["image"])
    # numeric-family defaults and bounds; each property is optional on its model
    for arg, prop in (("value", "DefaultValue"), ("min", "ValueMin"),
                      ("max", "ValueMax"), ("decimals", "DecimalAccuracy"),
                      ("format", "EditMask"), ("currency", "CurrencySymbol")):
        if args.get(arg) is not None:
            try:
                setattr(model, prop, args[arg])
            except Exception:
                pass          # not every kind carries every one of these
    if args.get("required") is not None:
        try:
            model.Required = bool(args["required"])
        except Exception:
            pass
    if args.get("readonly") is not None:
        try:
            model.ReadOnly = bool(args["readonly"])
        except Exception:
            pass
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


# --------------------------------------------------------------------------- #
# Tools borrowed/pruned from sibling LibreOffice-MCP projects (see TODO.md).
# All fit the existing Writer/Calc/cross-app model; new-app stuff (Base, Impress,
# Draw) is intentionally NOT here — see docs/UPSTREAM-PARITY.md.
# --------------------------------------------------------------------------- #

def tool_run_python_macro(args):
    """Invoke a PYTHON macro via the script provider (complements run_macro's
    Basic). 'name' is a full vnd.sun.star.script: URI, or 'file.py$function'
    resolved at 'location' (user/share/document; default user). Returns the value."""
    doc = _current_doc()
    name = str(args["name"])
    if name.startswith("vnd.sun.star.script:"):
        uri = name
    else:
        loc = str(args.get("location", "user"))
        uri = "vnd.sun.star.script:%s?language=Python&location=%s" % (name, loc)
    script = doc.getScriptProvider().getScript(uri)
    invoked = script.invoke(tuple(args.get("args") or ()), (), ())
    ret = invoked[0] if isinstance(invoked, tuple) and invoked else None
    try:
        json.dumps(ret)
    except Exception:
        ret = str(ret)
    return {"invoked": uri, "returned": ret}


def tool_list_macros(_args):
    """Discover macros: document Basic libraries -> modules, and user Python
    script files. Best-effort (application Basic isn't always enumerable)."""
    out = {"basic": {}, "python": []}
    doc = _current_doc()
    libs = getattr(doc, "BasicLibraries", None)
    if libs is not None:
        try:
            for lib in libs.getElementNames():
                try:
                    if not libs.isLibraryLoaded(lib):
                        libs.loadLibrary(lib)
                    out["basic"][lib] = list(libs.getByName(lib).getElementNames())
                except Exception:
                    out["basic"][lib] = []
        except Exception:
            pass
    try:
        import unohelper
        state = _connect()
        ps = state["smgr"].createInstanceWithContext(
            "com.sun.star.util.PathSettings", state["ctx"])
        for u in (getattr(ps, "Basic", "") or "").split(";"):
            u = u.strip()
            if not u:
                continue
            d = unohelper.fileUrlToSystemPath(u) if u.startswith("file:") else u
            pyd = os.path.join(d, "..", "Scripts", "python")
            if os.path.isdir(pyd):
                for f in os.listdir(pyd):
                    if f.lower().endswith(".py"):
                        out["python"].append(os.path.join(os.path.normpath(pyd), f))
    except Exception:
        pass
    return out


def tool_convert(args):
    """Headlessly convert document(s) to another format via LibreOffice filters
    (e.g. docx/xlsx -> pdf, odt -> docx). Give 'path' (one) or 'paths' (many) and
    'to' (target format). Outputs land beside each source, or in 'output_dir'.
    Each file is loaded hidden, stored, and closed — the active doc is untouched."""
    to = str(args.get("to", "pdf")).lower()
    paths = list(args.get("paths") or ([args["path"]] if args.get("path") else []))
    if not paths:
        raise RuntimeError("Give 'path' or 'paths'.")
    out_dir = args.get("output_dir")
    desktop = _desktop()
    hidden = (_pv("Hidden", True),)
    ext = {"pdf": "pdf", "odt": "odt", "docx": "docx", "txt": "txt",
           "ods": "ods", "xlsx": "xlsx", "csv": "csv"}.get(to, to)
    results = []
    for p in paths:
        if not os.path.exists(p):
            raise RuntimeError("File not found: %s" % p)
        doc = desktop.loadComponentFromURL(_to_url(p), "_blank", 0, hidden)
        if doc is None:
            raise RuntimeError("Could not open: %s" % p)
        try:
            kind = _doc_kind(doc)
            filt = _FILTERS.get((kind, to))
            if filt is None:
                raise RuntimeError("Cannot convert a %s document to %r. Options: %s"
                                   % (kind, to,
                                      sorted(f for k, f in _FILTERS if k == kind)))
            base = os.path.splitext(os.path.basename(p))[0]
            dest_dir = out_dir or os.path.dirname(os.path.abspath(p))
            out_path = os.path.join(dest_dir, base + "." + ext)
            doc.storeToURL(_to_url(out_path), (_pv("FilterName", filt),))
            results.append({"input": p, "output": os.path.abspath(out_path)})
        finally:
            try:
                doc.close(False)
            except Exception:
                pass
    return {"converted": results, "to": to, "count": len(results)}


def tool_merge(args):
    """Merge several Writer/text documents into one, in order, with a page break
    between them; save to 'output'. Text documents only — Calc/PDF merging is out
    of scope (see docs/UPSTREAM-PARITY.md)."""
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    paths = list(args.get("paths") or [])
    if len(paths) < 2:
        raise RuntimeError("Give 'paths': at least two documents to merge.")
    output = args.get("output")
    if not output:
        raise RuntimeError("Give 'output': the merged file path.")
    for p in paths:
        if not os.path.exists(p):
            raise RuntimeError("File not found: %s" % p)
    base = _desktop().loadComponentFromURL(
        "private:factory/swriter", "_blank", 0, (_pv("Hidden", True),))
    try:
        text = base.getText()
        for i, p in enumerate(paths):
            # Re-fetch the end cursor each time — insertDocumentFromURL leaves the
            # prior cursor stale, so a single reused cursor drops earlier docs.
            cursor = text.createTextCursorByRange(text.getEnd())
            if i > 0:
                text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
                try:
                    cursor.BreakType = _uno_enum(
                        "com.sun.star.style.BreakType", "PAGE_BEFORE")
                except Exception:
                    pass
            cursor.insertDocumentFromURL(_to_url(p), ())
        ext = os.path.splitext(output)[1].lstrip(".").lower()
        fmt = ext if ("writer", ext) in _FILTERS else "odt"
        base.storeToURL(_to_url(output),
                        (_pv("FilterName", _FILTERS[("writer", fmt)]),
                         _pv("Overwrite", True)))
    finally:
        try:
            base.close(False)
        except Exception:
            pass
    return {"merged": os.path.abspath(output), "sources": len(paths)}


def tool_list_templates(_args):
    """List document templates under LibreOffice's configured Template paths.
    Returns [{name, path}] plus the directories scanned."""
    import unohelper
    state = _connect()
    ps = state["smgr"].createInstanceWithContext(
        "com.sun.star.util.PathSettings", state["ctx"])
    dirs, out = [], []
    for u in (getattr(ps, "Template", "") or "").split(";"):
        u = u.strip()
        if not u:
            continue
        d = unohelper.fileUrlToSystemPath(u) if u.startswith("file:") else u
        dirs.append(d)
        if not os.path.isdir(d):
            continue
        for root, _dd, files in os.walk(d):
            for f in files:
                if f.lower().endswith((".ott", ".ots", ".otp", ".otg",
                                       ".stw", ".stc")):
                    out.append({"name": os.path.splitext(f)[0],
                                "path": os.path.join(root, f)})
    return {"templates": out, "count": len(out), "dirs": dirs}


def tool_create_from_template(args):
    """Create a new untitled document from a template file (.ott/.ots/…)."""
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("Template not found: %s" % path)
    doc = _desktop().loadComponentFromURL(
        _to_url(path), "_blank", 0, (_pv("AsTemplate", True),))
    if doc is None:
        raise RuntimeError("Could not create from template: %s" % path)
    _activate(doc)
    return {"created": _doc_info(doc), "from_template": os.path.abspath(path)}


def tool_dispatch(args):
    """Portmanteau facade: run ANY of this server's tools by name — for MCP
    clients with a tool-count cap. args: {"tool": "<name>", "args": {...}}. Omit
    'tool' (or use 'list'/'help') for the catalog of names + one-line usage. Does
    NOT replace the discrete tools; it fans out to the very same handlers."""
    name = args.get("tool") or args.get("name")
    if not name or str(name).lower() in ("list", "help", "?"):
        return {"tools": [{"name": d["name"], "description": d["description"]}
                          for d in TOOL_DEFS]}
    name = str(name)
    if name == "dispatch":
        raise RuntimeError("Refusing to dispatch to 'dispatch' (recursion).")
    fn = TOOLS.get(name)
    if fn is None:
        raise RuntimeError("No tool named %r — use tool='list' for the catalog."
                           % name)
    # via _call_with_reconnect so a dispatched tool keeps its undo grouping
    return _call_with_reconnect(fn, args.get("args") or {}, name)


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
        # --- accessibility ---
        if args.get("tagged"):
            fd.append(_pv("UseTaggedPDF", True))
        if args.get("pdfua"):
            # PDF/UA-1 implies a tagged PDF; ask for both so the option cannot
            # be silently ineffective when 'tagged' was left out
            fd.append(_pv("PDFUACompliance", True))
            fd.append(_pv("UseTaggedPDF", True))
        if args.get("bookmarks") is not None:
            fd.append(_pv("ExportBookmarks", bool(args["bookmarks"])))
        # --- fillable forms ---
        if args.get("form_fields"):
            fd.append(_pv("ExportFormFields", True))
            fd.append(_pv("FormsType", int(args.get("forms_type", 1))))
        # --- owner password + permissions (distinct from the OPEN password) ---
        if args.get("owner_password"):
            fd.append(_pv("EncryptFile", True))
            fd.append(_pv("RestrictPermissions", True))
            fd.append(_pv("PermissionPassword", str(args["owner_password"])))
            for arg, prop in (("can_print", "CanPrint"),
                              ("can_modify", "CanModify"),
                              ("can_copy", "CanCopyOrExtract"),
                              ("can_annotate", "CanAddOrModifyAnnotations")):
                if args.get(arg) is not None:
                    fd.append(_pv(prop, bool(args[arg])))
        if args.get("watermark"):
            fd.append(_pv("Watermark", str(args["watermark"])))
        filter_name = {"writer": "writer_pdf_Export",
                       "impress": "impress_pdf_Export",
                       "draw": "draw_pdf_Export"}.get(_doc_kind(doc),
                                                      "calc_pdf_Export")
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
    # Dublin Core, as shown in File > Properties > Description. Five take a
    # plain string; three are sequence<string> and raise CannotConvertException
    # if handed a bare string (verified against 25.2.3.2) — same shape as
    # Keywords, so they go through the same coercion.
    for key, prop in (("coverage", "Coverage"), ("identifier", "Identifier"),
                      ("rights", "Rights"), ("source", "Source"),
                      ("type", "Type")):
        if args.get(key) is not None:
            setattr(props, prop, str(args[key]))
            changed.append(prop)
    for key, prop in (("keywords", "Keywords"),
                      ("contributor", "Contributor"),
                      ("publisher", "Publisher"), ("relation", "Relation")):
        if args.get(key) is not None:
            value = args[key]
            setattr(props, prop,
                    tuple(str(v) for v in value)
                    if isinstance(value, (list, tuple)) else (str(value),))
            changed.append(prop)
    if args.get("language"):
        # the document language: what a screen reader announces, and what
        # spellcheck and a tagged PDF both key off
        tag = str(args["language"]).replace("_", "-")
        parts = tag.split("-")
        locale = _uno_struct("com.sun.star.lang.Locale")
        locale.Language = parts[0]
        locale.Country = parts[1].upper() if len(parts) > 1 else ""
        props.Language = locale
        changed.append("Language")
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


def _blank_paragraph_at(text, where, before):
    """Open an empty paragraph next to `where` and return a cursor sitting in it.

    `before=True` puts the new paragraph ahead of `where`'s content, which needs
    the extra gotoPreviousParagraph — inserting a break at a paragraph's start
    leaves the cursor with the ORIGINAL content, not the new empty line.
    """
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    cur = text.createTextCursorByRange(where)
    text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
    if before:
        cur.gotoPreviousParagraph(False)
    return cur


def _caption_slot_for_table(doc, name, position):
    """A table's getAnchor() is NOT a usable text range on this build —
    getText() returns None and createTextCursorByRange rejects it. So find the
    table in the body enumeration and work from the paragraph beside it."""
    text = doc.getText()
    elements = []
    enum = text.createEnumeration()
    while enum.hasMoreElements():
        elements.append(enum.nextElement())
    idx = None
    for i, el in enumerate(elements):
        if (el.supportsService("com.sun.star.text.TextTable")
                and getattr(el, "Name", None) == name):
            idx = i
            break
    if idx is None:
        raise RuntimeError("No table named %r. Tables: %s"
                           % (name, ", ".join(doc.getTextTables().getElementNames())))
    if position == "before":
        if idx == 0:
            raise RuntimeError(
                "Table %r is the first thing in the document, so there is no "
                "paragraph above it to hold a caption — use position='after'."
                % name)
        return text, _blank_paragraph_at(text, elements[idx - 1].getEnd(), False)
    if idx + 1 >= len(elements):
        return text, _blank_paragraph_at(text, text.getEnd(), False)
    return text, _blank_paragraph_at(text, elements[idx + 1].getStart(), True)


def _caption_slot_for_image(doc, name, position):
    graphics = doc.getGraphicObjects()
    if not graphics.hasByName(name):
        raise RuntimeError("No image named %r. Images: %s"
                           % (name, ", ".join(graphics.getElementNames())))
    rng = graphics.getByName(name).getAnchor()
    host = rng.getText()
    if host is None:                     # same quirk as tables — fall back
        host = doc.getText()
        return host, _blank_paragraph_at(host, host.getEnd(), False)
    return host, _blank_paragraph_at(
        host, rng.getStart() if position == "before" else rng.getEnd(),
        position == "before")


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
    # Convention: table captions sit above the table, figure captions below it —
    # so 'position' defaults per object type rather than globally.
    if args.get("table"):
        text, cur = _caption_slot_for_table(
            doc, args["table"], str(args.get("position") or "before").lower())
    elif args.get("image"):
        text, cur = _caption_slot_for_image(
            doc, args["image"], str(args.get("position") or "after").lower())
    elif args.get("search"):
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
            "text": label,
            "anchored_to": args.get("table") or args.get("image") or None}


_SEQ_FIELD = "com.sun.star.text.TextField.SetExpression"


def _iter_captions(doc):
    """Yield (field, category, paragraph_cursor) for every auto-numbered caption.

    A caption is a SetExpression field of subtype SEQUENCE — the same thing
    writer_insert_caption creates, and the same thing LibreOffice's own
    Insert > Caption creates, so captions made in the GUI are found too.
    """
    from com.sun.star.text.SetVariableType import SEQUENCE
    enum = doc.getTextFields().createEnumeration()
    while enum.hasMoreElements():
        field = enum.nextElement()
        if not field.supportsService(_SEQ_FIELD):
            continue
        try:
            if field.SubType != SEQUENCE:
                continue
        except Exception:
            continue
        anchor = field.getAnchor()
        # a caption can live in a table cell or a frame, so walk ITS text rather
        # than doc.getText()
        host = anchor.getText()
        cur = host.createTextCursorByRange(anchor)
        cur.gotoStartOfParagraph(False)
        cur.gotoEndOfParagraph(True)
        try:
            category = field.getTextFieldMaster().Name
        except Exception:
            category = ""
        yield field, category, cur


def tool_writer_captions(args):
    """List or re-word the auto-numbered captions ('Figure 1 - Site plan').

    The NUMBER is a field and stays owned by LibreOffice, so renumbering after
    an insert or delete keeps working; only the label is rewritten.
    """
    doc = _require_writer()
    action = str(args.get("action", "list")).lower()
    if action not in ("list", "set"):
        raise RuntimeError("action must be 'list' or 'set'. To remove a caption "
                           "entirely, use writer_delete_paragraphs on its text.")

    found = []
    for i, (field, category, cur) in enumerate(_iter_captions(doc)):
        number = field.getPresentation(False)
        full = cur.getString()
        label, pos = full, full.find(number)
        if pos >= 0:
            label = full[pos + len(number):]
        found.append({"index": i, "category": category, "number": number,
                      "label": label.lstrip(" —-:.\t"), "text": full,
                      "_field": field})

    if action == "list":
        return {"captions": [{k: v for k, v in c.items()
                              if not k.startswith("_")} for c in found],
                "count": len(found)}

    want_index = args.get("index")
    search = (args.get("search") or "").lower()
    category = (args.get("category") or "").lower()
    if want_index is None and not search and not category:
        raise RuntimeError("Give 'index' (from action='list'), 'search' (text "
                           "in the caption) or 'category' to pick the caption.")
    if "text" not in args:
        raise RuntimeError("Give 'text' - the new caption label.")
    sep = args.get("separator", " — ")

    changed = []
    for c in found:
        if want_index is not None and c["index"] != int(want_index):
            continue
        if search and search not in c["text"].lower():
            continue
        if category and category != c["category"].lower():
            continue
        anchor = c["_field"].getAnchor()
        host = anchor.getText()
        tail = host.createTextCursorByRange(anchor.getEnd())
        tail.gotoEndOfParagraph(True)          # everything after the number
        tail.setString(sep + args["text"])
        changed.append({"index": c["index"], "category": c["category"],
                        "number": c["number"], "label": args["text"]})
    if not changed:
        raise RuntimeError("No caption matched - call this tool with "
                           "action='list' to see the captions and their indexes.")
    try:
        doc.getTextFields().refresh()
    except Exception:
        pass
    return {"updated": changed, "count": len(changed)}


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
    search = args.get("search") or ""
    style = (args.get("style") or "").strip()
    if not search and not style:
        raise RuntimeError("Give 'search' text, a paragraph 'style' to list, "
                           "or both.")
    mc = bool(args.get("match_case", False))
    limit = int(args.get("limit", 100))

    matcher = None
    if search:
        if args.get("regex"):
            import re
            try:
                matcher = re.compile(search, 0 if mc else re.IGNORECASE)
            except re.error as exc:
                raise RuntimeError("Bad regular expression %r: %s" % (search, exc))
        else:
            needle = search if mc else search.lower()

    out = []
    for i, para in _writer_paragraphs(doc):
        para_style = para.getPropertyValue("ParaStyleName")
        if style and para_style.lower() != style.lower():
            continue
        s = para.getString()
        if matcher is not None:
            hits = list(matcher.finditer(s))
            if not hits:
                continue
            pos, length, count = hits[0].start(), len(hits[0].group(0)), len(hits)
        elif search:
            hay = s if mc else s.lower()
            pos = hay.find(needle)
            if pos == -1:
                continue
            length, count = len(search), hay.count(needle)
        else:                     # style-only listing
            pos, length, count = 0, 0, 0

        a = max(0, pos - 20)
        b = min(len(s), pos + length + 20)
        snippet = ("…" if a > 0 else "") + s[a:b] + ("…" if b < len(s) else "")
        out.append({"paragraph": i, "occurrences": count, "snippet": snippet,
                    "style": para_style})
        if len(out) >= limit:
            break
    return {"matches": out, "paragraphs_matched": len(out),
            "total_occurrences": sum(m["occurrences"] for m in out),
            "regex": bool(args.get("regex")), "style_filter": style or None}


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
            results.append({"tool": name, "ok": True,
                            "result": _call_with_reconnect(fn, a, name)})
        except Exception as exc:  # surface, don't abort the whole batch silently
            results.append({"tool": name, "ok": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            if stop:
                break
    return {"results": results, "count": len(results),
            "ok": all(r["ok"] for r in results)}


# --------------------------------------------------------------------------- #
# Tools — everyday composites (task altitude, not UNO altitude)
#
# The rest of this server is one tool per UNO property group, which is right for
# an operator who knows what they want. An everyday ask ("make this table look
# nice") otherwise costs 5+ round-trips of format_range + set_borders +
# set_dimensions + sheet_properties. These collapse the common ones into one
# call and are the tools the `basic` tier advertises.
# --------------------------------------------------------------------------- #

def _sheet_used_addr(sheet):
    cur = sheet.createCursor()
    cur.gotoStartOfUsedArea(False)
    cur.gotoEndOfUsedArea(True)
    return cur.getRangeAddress()


def _addr_is_empty(sheet, addr):
    """A never-touched sheet still reports a 1x1 used area at A1."""
    if addr.EndRow != addr.StartRow or addr.EndColumn != addr.StartColumn:
        return False
    return not sheet.getCellByPosition(addr.StartColumn, addr.StartRow).getString()


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


def _looks_numeric(text):
    """True when Calc would store this as a number if typed in. Rejects Python's
    own float() extras ('nan', 'inf', '1_000') — none of them are what a user
    means by 'this column should be numbers'."""
    if not text or "_" in text:
        return False
    if text.strip().lstrip("+-").lower() in ("nan", "inf", "infinity"):
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def _clean_cell(value):
    """Trim a literal text cell, and let numeric-looking text become a number.

    getFormulaArray() renders a TEXT cell that LOOKS numeric with a leading
    apostrophe — Calc's force-text marker — so " 3 " arrives as "' 3 ". Dropping
    that marker is what turns the cell back into a real number on write-back; a
    plain .strip() leaves "' 3" and the cell stays stubbornly text.
    """
    if not isinstance(value, str):
        return value
    if value.startswith("'"):
        body = value[1:].strip()
        # only un-text it when it really is a number — otherwise keep the marker
        return body if _looks_numeric(body) else "'" + body
    return value.strip()


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


# preset -> (body font, body pt, margin mm, line spacing %)
_DOC_PRESETS = {
    "report": ("Liberation Sans", 11.0, 20.0, 115),
    "essay":  ("Liberation Serif", 12.0, 25.4, 200),
    "letter": ("Liberation Serif", 12.0, 25.0, 100),
}


def tool_writer_format_document(args):
    """One call for 'make this document presentable': base typography, page
    margins and line spacing for a common document shape."""
    doc = _require_writer()
    preset = str(args.get("preset", "report")).lower()
    if preset not in _DOC_PRESETS:
        raise RuntimeError("preset must be one of %s" % sorted(_DOC_PRESETS))
    font, size, margin_mm, spacing = _DOC_PRESETS[preset]
    font = args.get("font_name") or font
    size = float(args.get("font_size") or size)
    applied = []

    std = doc.getStyleFamilies().getByName("ParagraphStyles").getByName("Standard")
    for attr, value in (("CharFontName", font), ("CharFontNameComplex", font),
                        ("CharFontNameAsian", font), ("CharHeight", size),
                        ("CharHeightComplex", size), ("CharHeightAsian", size)):
        setattr(std, attr, value)
    applied.append("typography")

    try:
        line = _uno_struct("com.sun.star.style.LineSpacing")
        line.Mode = 2  # PROP — proportional, Height is a percentage
        line.Height = int(spacing)
        std.ParaLineSpacing = line
        applied.append("line_spacing")
    except Exception:
        pass

    styles = doc.getStyleFamilies().getByName("PageStyles")
    name = doc.getCurrentController().getViewCursor().PageStyleName
    page = styles.getByName(name if styles.hasByName(name) else "Standard")
    mm100 = int(round(margin_mm * 100))
    for side in ("TopMargin", "BottomMargin", "LeftMargin", "RightMargin"):
        setattr(page, side, mm100)
    applied.append("margins")

    return {"preset": preset, "font_name": font, "font_size": size,
            "margin_mm": margin_mm, "line_spacing_percent": spacing,
            "page_style": page.Name, "applied": applied}


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


def tool_list_recent_documents(args):
    """The Files > Recent Documents list, so "open my last essay" works without
    the user having to remember where they saved it."""
    state = _connect()
    provider = state["smgr"].createInstanceWithContext(
        "com.sun.star.configuration.ConfigurationProvider", state["ctx"])
    node = provider.createInstanceWithArguments(
        "com.sun.star.configuration.ConfigurationAccess",
        (_pv("nodepath", "/org.openoffice.Office.Histories/Histories"),))
    items = node.getByName("PickList").getByName("ItemList")

    limit = int(args.get("limit", 15))
    out = []
    for name in list(items.getElementNames())[:limit]:
        item = items.getByName(name)
        entry = {}
        for prop, key in (("URL", "url"), ("Title", "title"), ("Filter", "filter")):
            try:
                entry[key] = item.getPropertyValue(prop)
            except Exception:
                pass
        if entry.get("url"):
            try:
                import unohelper
                entry["path"] = unohelper.fileUrlToSystemPath(entry["url"])
            except Exception:
                pass
        out.append(entry)
    return {"recent": out, "count": len(out)}


def tool_print_document(args):
    """Send a document to a PHYSICAL printer."""
    doc = _select_doc(args) or _current_doc()
    opts = []
    if args.get("printer"):
        opts.append(_pv("Name", str(args["printer"])))
    if args.get("pages"):
        opts.append(_pv("Pages", str(args["pages"])))
    copies = int(args.get("copies", 1))
    if copies != 1:
        opts.append(_pv("CopyCount", copies))
    opts.append(_pv("Wait", True))   # so a failure surfaces here, not silently
    # "print" is a keyword in Python 2-era UNO bindings; both spellings exist.
    printer = getattr(doc, "print_", None) or getattr(doc, "print")
    printer(tuple(opts))
    return {"printed": _doc_info(doc),
            "printer": args.get("printer") or "system default",
            "pages": args.get("pages") or "all", "copies": copies}


def tool_writer_resolve_comment(args):
    """Mark a comment resolved / unresolved — the other half of the review loop
    that writer_get_comments already reports but nothing could set."""
    doc = _require_writer()
    want_index = args.get("index")
    search = (args.get("search") or "").lower()
    author = (args.get("author") or "").lower()
    resolved = bool(args.get("resolved", True))
    if want_index is None and not search and not author:
        raise RuntimeError("Give 'index', 'search' (comment-text substring) or "
                           "'author' to pick which comment(s) to mark.")

    changed, i = [], -1
    enum = doc.getTextFields().createEnumeration()
    while enum.hasMoreElements():
        field = enum.nextElement()
        if not field.supportsService(_ANNOTATION):
            continue
        i += 1
        if want_index is not None and i != int(want_index):
            continue
        if search and search not in (field.Content or "").lower():
            continue
        if author and author not in (field.Author or "").lower():
            continue
        try:
            field.setPropertyValue("Resolved", resolved)
        except Exception as exc:
            raise RuntimeError(
                "This LibreOffice build cannot resolve comments (%s); the "
                "feature needs LibreOffice 7.1 or newer." % exc)
        changed.append({"index": i, "author": field.Author, "text": field.Content})
    if not changed:
        raise RuntimeError("No comment matched — call writer_get_comments to "
                           "see the list and its indexes.")
    return {"resolved": resolved, "changed": changed, "count": len(changed)}


# --------------------------------------------------------------------------- #
# Tools — failure recovery
#
# The auto-launch keeps --norestore ON PURPOSE: without it a pending crash makes
# LibreOffice open its recovery dialog at startup, and that dialog blocks the
# UNO socket from ever opening — auto-launch would hang instead of connecting.
# So recovery is not silently discarded, it is DETECTED here (lo_health,
# lo_recover) and restored deliberately over UNO, with no dialog involved.
# --------------------------------------------------------------------------- #

def _config_node(path, writable=False):
    state = _connect()
    provider = state["smgr"].createInstanceWithContext(
        "com.sun.star.configuration.ConfigurationProvider", state["ctx"])
    service = ("com.sun.star.configuration.ConfigurationUpdateAccess" if writable
               else "com.sun.star.configuration.ConfigurationAccess")
    return provider.createInstanceWithArguments(service, (_pv("nodepath", path),))


_RECOVERY_PATH = "/org.openoffice.Office.Recovery"


def _recovery_state():
    """What LibreOffice is holding for crash recovery, if anything."""
    out = {"crashed": False, "autosave_enabled": None,
           "autosave_minutes": None, "pending": []}
    try:
        rec = _config_node(_RECOVERY_PATH)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    try:
        info = rec.getByName("RecoveryInfo")
        out["crashed"] = bool(info.getPropertyValue("Crashed"))
    except Exception:
        pass
    try:
        auto = rec.getByName("AutoSave")
        out["autosave_enabled"] = bool(auto.getPropertyValue("Enabled"))
        out["autosave_minutes"] = int(auto.getPropertyValue("TimeIntervall"))
    except Exception:
        pass
    try:
        items = rec.getByName("RecoveryList")
        for name in items.getElementNames():
            entry, item = {}, items.getByName(name)
            for prop in ("OriginalURL", "TempURL", "Title", "Filter",
                         "DocumentState", "Module"):
                try:
                    entry[prop] = item.getPropertyValue(prop)
                except Exception:
                    pass
            out["pending"].append(entry)
    except Exception:
        pass
    return out


def tool_lo_recover(args):
    """Report and act on LibreOffice's crash-recovery state."""
    action = str(args.get("action", "status")).lower()
    # Validate BEFORE connecting: a bad action or a missing confirmation is the
    # caller's mistake and should not depend on an office being reachable.
    if action not in ("status", "restore", "discard", "set_autosave"):
        raise RuntimeError("action must be one of status, restore, discard, "
                           "set_autosave.")
    if action == "discard" and not args.get("confirm"):
        raise RuntimeError(
            "Discarding recovery data permanently destroys the unsaved work "
            "LibreOffice saved during the crash. Pass confirm=true only after "
            "the user has explicitly agreed.")

    state = _recovery_state()

    if action == "status":
        state["advice"] = (
            "Documents are waiting to be recovered — call this tool with "
            "action='restore' to reopen them." if state["pending"] else
            "Nothing is pending recovery." if not state["crashed"] else
            "LibreOffice recorded a crash but has nothing left to recover.")
        if not state.get("autosave_enabled"):
            state["advice"] += (" AutoSave is OFF; action='set_autosave' with "
                                "minutes=10 turns it on for next time.")
        return state

    if action == "restore":
        if not state["pending"]:
            return dict(state, restored=False,
                        note="Nothing was pending recovery, so nothing was done.")
        st = _connect()
        recovery = st["smgr"].createInstanceWithContext(
            "com.sun.star.frame.AutoRecovery", st["ctx"])
        url = _uno_struct("com.sun.star.util.URL")
        url.Complete = "vnd.sun.star.autorecovery:/doAutoRestore"
        try:      # the URL must be parsed before a dispatcher will accept it
            parser = st["smgr"].createInstanceWithContext(
                "com.sun.star.util.URLTransformer", st["ctx"])
            _, url = parser.parseStrict(url)
        except Exception:
            pass
        recovery.dispatch(url, (_pv("SetAutoRecoveryState", True),))
        return {"restored": True, "documents": state["pending"],
                "note": "Recovered documents are now open — save them to a real "
                        "location before doing anything else."}

    if action == "discard":
        node = _config_node(_RECOVERY_PATH, writable=True)
        items = node.getByName("RecoveryList")
        removed = list(items.getElementNames())
        for name in removed:
            try:
                items.removeByName(name)
            except Exception:
                pass
        node.commitChanges()
        return {"discarded": removed, "count": len(removed)}

    # the only action left; the set is validated at the top
    minutes = int(args.get("minutes", 10))
    node = _config_node(_RECOVERY_PATH, writable=True)
    auto = node.getByName("AutoSave")
    auto.setPropertyValue("Enabled", minutes > 0)
    if minutes > 0:
        auto.setPropertyValue("TimeIntervall", minutes)
    node.commitChanges()
    return {"autosave_enabled": minutes > 0, "autosave_minutes": minutes}


def _checkpoint_dir():
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "claude-lo-checkpoints")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def tool_checkpoint_document(args):
    """Snapshot a document to a side file so a risky edit can be rolled back.

    This is the rollback that undo cannot give: LibreOffice does not record bulk
    range writes for undo at all (docs/KNOWN-GAPS.md), so for anything that
    rewrites a range, a checkpoint is the ONLY way back.
    """
    import shutil
    import time

    action = str(args.get("action", "create")).lower()
    folder = _checkpoint_dir()

    if action == "list":
        rows = []
        for name in sorted(os.listdir(folder), reverse=True):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                rows.append({"id": name, "path": full,
                             "size_bytes": os.path.getsize(full),
                             "saved_at": time.strftime(
                                 "%Y-%m-%d %H:%M:%S",
                                 time.localtime(os.path.getmtime(full)))})
        return {"checkpoints": rows, "count": len(rows), "folder": folder}

    if action == "create":
        doc = _select_doc(args) or _current_doc()
        info = _doc_info(doc)
        original = doc.getURL() or ""
        # strip the title's own extension first, else "report.ods" sanitises to
        # "reportods" and the id reads as gibberish
        base = os.path.splitext(info.get("title") or "untitled")[0]
        stem = "".join(c for c in base
                       if c.isalnum() or c in "-_ ").strip() or "untitled"
        ext = os.path.splitext(original)[1] or ".ods"
        cid = "%s__%s%s" % (time.strftime("%Y%m%d-%H%M%S"), stem, ext)
        target = os.path.join(folder, cid)
        # storeToURL writes a COPY and leaves the live document's own location
        # and modified flag alone — storeAsURL would silently re-point it here.
        doc.storeToURL(_to_url(target), ())
        return {"checkpoint_id": cid, "path": target, "of": info,
                "original_url": original,
                "note": "Roll back with action='restore' and this checkpoint_id."}

    if action == "restore":
        cid = args.get("checkpoint_id")
        if not cid:
            raise RuntimeError("Give 'checkpoint_id' — action='list' shows them.")
        source = os.path.join(folder, os.path.basename(cid))
        if not os.path.isfile(source):
            raise RuntimeError("No checkpoint %r. Call action='list'." % cid)

        doc = _select_doc(args) or _current_doc()
        original = doc.getURL() or ""
        if not original:
            # never saved — there is nothing to restore OVER, so just open it
            opened = _desktop().loadComponentFromURL(
                _to_url(source), "_blank", 0, ())
            _activate(opened)
            return {"restored": "opened_alongside", "path": source,
                    "note": "The live document has never been saved, so the "
                            "checkpoint was opened as a separate document "
                            "instead of overwriting anything."}

        import unohelper
        target_path = unohelper.fileUrlToSystemPath(original)
        doc.setModified(False)      # discard in-memory edits, then close
        doc.close(False)
        shutil.copyfile(source, target_path)
        reopened = _desktop().loadComponentFromURL(_to_url(target_path),
                                                   "_blank", 0, ())
        _activate(reopened)
        return {"restored": target_path, "from_checkpoint": cid,
                "document": _doc_info(reopened)}

    raise RuntimeError("action must be one of create, list, restore.")


# doc key -> {"listener", "doc", "total", "ours"}. A UNO modify callback lands on
# a bridge thread, and the timeout wrapper runs tools on a worker thread, so
# every touch of this goes through _WATCH_LOCK.
_WATCHERS = {}
_WATCH_LOCK = None


def _watch_lock():
    global _WATCH_LOCK
    if _WATCH_LOCK is None:
        import threading
        _WATCH_LOCK = threading.Lock()
    return _WATCH_LOCK


def _watch_key(doc):
    try:
        return doc.getURL() or doc.getTitle()
    except Exception:
        return "active"


def _note_our_edit():
    """Called after every successful mutating tool, so a watcher can subtract
    the edits WE made from the edits the user made."""
    if not _WATCHERS:
        return
    with _watch_lock():
        for entry in _WATCHERS.values():
            entry["ours"] += 1


def _make_watcher():
    import unohelper
    from com.sun.star.util import XModifyListener

    class _Watcher(unohelper.Base, XModifyListener):
        def __init__(self, entry):
            self.entry = entry

        def modified(self, _event):
            with _watch_lock():
                self.entry["total"] += 1

        def disposing(self, _event):
            self.entry["disposed"] = True

    return _Watcher


def tool_document_watch(args):
    """Detect that a document changed under us — including edits the USER made
    while Claude was thinking, which is the case worth guarding against before
    overwriting anything."""
    action = str(args.get("action", "check")).lower()
    if action not in ("start", "check", "stop", "list"):
        raise RuntimeError("action must be one of start, check, stop, list.")

    if action == "list":
        with _watch_lock():
            return {"watching": [
                {"document": key, "total_changes": e["total"],
                 "our_edits": e["ours"],
                 "user_edits": max(0, e["total"] - e["ours"])}
                for key, e in _WATCHERS.items()], "count": len(_WATCHERS)}

    doc = _select_doc(args) or _current_doc()
    key = _watch_key(doc)

    if action == "start":
        if key in _WATCHERS:                     # restart cleanly
            tool_document_watch({"action": "stop", "url": key})
        entry = {"total": 0, "ours": 0, "doc": doc}
        listener = _make_watcher()(entry)
        doc.addModifyListener(listener)
        entry["listener"] = listener
        with _watch_lock():
            _WATCHERS[key] = entry
        return {"watching": key, "document": _doc_info(doc),
                "note": "Call action='check' later to see whether the user "
                        "edited it in the meantime."}

    if action == "stop":
        entry = _WATCHERS.pop(key, None)
        if entry is None:
            return {"watching": None, "note": "That document was not watched."}
        try:
            entry["doc"].removeModifyListener(entry["listener"])
        except Exception:
            pass
        return {"stopped": key, "total_changes": entry["total"],
                "our_edits": entry["ours"],
                "user_edits": max(0, entry["total"] - entry["ours"])}

    entry = _WATCHERS.get(key)
    if entry is None:
        raise RuntimeError("Nothing is watching %r yet — call action='start' "
                           "before the step you want to guard." % key)
    with _watch_lock():
        total, ours = entry["total"], entry["ours"]
    user_edits = max(0, total - ours)
    return {"document": key, "total_changes": total, "our_edits": ours,
            "user_edits": user_edits,
            "changed_by_user": bool(user_edits),
            "advice": ("The user edited this document since the watch started — "
                       "re-read before overwriting." if user_edits else
                       "No edits from the user since the watch started.")}


def tool_lo_health(args):
    """Pre-flight: is it safe to start editing, and is anything at risk?"""
    state = _connect()
    report = {"connected": True, "transport": state.get("transport"),
              "call_timeout_seconds": _call_timeout(),
              "tools_advertised": len(_advertised_tools()),
              "tools_total": len(TOOLS)}
    problems = []

    docs = []
    for doc in _open_docs():
        info = _doc_info(doc)
        try:
            info["unsaved_changes"] = bool(doc.isModified())
        except Exception:
            info["unsaved_changes"] = None
        try:
            info["has_file"] = bool(doc.hasLocation())
        except Exception:
            info["has_file"] = None
        # a .~lock.<name># left behind by a crash blocks the next open
        url = doc.getURL() or ""
        if url:
            try:
                import unohelper
                path = unohelper.fileUrlToSystemPath(url)
                lock = os.path.join(os.path.dirname(path),
                                    ".~lock.%s#" % os.path.basename(path))
                info["lock_file"] = lock if os.path.exists(lock) else None
            except Exception:
                pass
        if info.get("unsaved_changes"):
            problems.append("%s has unsaved changes — checkpoint_document or "
                            "save_document before a risky edit."
                            % info.get("title"))
        docs.append(info)
    report["documents"] = docs

    recovery = _recovery_state()
    report["recovery"] = recovery
    if recovery.get("pending"):
        problems.append("%d document(s) are waiting to be recovered from a "
                        "crash — call lo_recover with action='restore'."
                        % len(recovery["pending"]))
    if recovery.get("autosave_enabled") is False:
        problems.append("AutoSave is off; lo_recover action='set_autosave' "
                        "minutes=10 reduces what a crash can cost.")
    if not docs:
        problems.append("No document is open — create_document, open_document "
                        "or list_recent_documents.")

    report["problems"] = problems
    report["healthy"] = not problems
    return report


# --------------------------------------------------------------------------- #
# Tools — print setup, accessibility, content controls
# --------------------------------------------------------------------------- #

# Writer and Calc expose different Print* switches; each list is what that
# application's document-settings object actually carries.
_PRINT_SETTINGS_WRITER = (
    "PrintGraphics", "PrintTables", "PrintDrawings", "PrintControls",
    "PrintPageBackground", "PrintBlackFonts", "PrintEmptyPages",
    "PrintHiddenText", "PrintTextPlaceholder", "PrintLeftPages",
    "PrintRightPages", "PrintReversed", "PrintProspect", "PrintProspectRTL",
    "PrintPaperFromSetup", "PrintFaxName", "PrintAnnotationMode",
)
_PRINT_SETTINGS_CALC = (
    "PrintAllSheets", "PrintEmptyPages", "PrintAnnotations", "PrintGrid",
    "PrintHeaders", "PrintCharts", "PrintObjects", "PrintDrawing",
    "PrintDownFirst", "PrintFormulas", "PrintNotes", "PrintZeroValues",
)

_PAPER_FORMATS = ("A3", "A4", "A5", "B4", "B5", "LETTER", "LEGAL", "TABLOID",
                  "USER")


def tool_print_settings(args):
    """Read or change how a document prints — printer, paper, orientation, and
    the per-application 'what to include' switches."""
    doc = _select_doc(args) or _current_doc()
    kind = _doc_kind(doc)
    wanted = _PRINT_SETTINGS_CALC if kind == "calc" else _PRINT_SETTINGS_WRITER
    changed = []

    # --- printer / paper (XPrintable) ---
    printer = []
    if args.get("printer"):
        printer.append(_pv("Name", str(args["printer"])))
        changed.append("printer")
    if args.get("orientation"):
        value = str(args["orientation"]).upper()
        if value not in ("PORTRAIT", "LANDSCAPE"):
            raise RuntimeError("orientation must be portrait or landscape.")
        printer.append(_pv("PaperOrientation",
                           _uno_enum("com.sun.star.view.PaperOrientation", value)))
        changed.append("orientation")
    if args.get("paper"):
        value = str(args["paper"]).upper()
        if value not in _PAPER_FORMATS:
            raise RuntimeError("paper must be one of %s" % (_PAPER_FORMATS,))
        printer.append(_pv("PaperFormat",
                           _uno_enum("com.sun.star.view.PaperFormat", value)))
        changed.append("paper")
    if printer:
        doc.setPrinter(tuple(printer))

    # --- the document's own print switches ---
    options = args.get("options") or {}
    settings = doc.createInstance("com.sun.star.document.Settings")
    for name, value in options.items():
        if name not in wanted:
            raise RuntimeError(
                "%r is not a print option for a %s document. Available: %s"
                % (name, kind, ", ".join(wanted)))
        settings.setPropertyValue(name, bool(value))
        changed.append(name)

    current = {}
    for name in wanted:
        try:
            current[name] = settings.getPropertyValue(name)
        except Exception:
            pass
    return {"document": _doc_info(doc), "application": kind,
            "printer": {p.Name: str(p.Value) for p in doc.getPrinter()},
            "options": current, "changed": changed,
            "available_options": list(wanted)}


def tool_set_alt_text(args):
    """Give an image or shape alternative text. Without this a tagged PDF is
    still inaccessible: the structure is there but every picture is silent."""
    ub = _bridge()
    doc = _select_doc(args) or _current_doc()
    name = args.get("name")
    title = args.get("title")
    description = args.get("description")
    decorative = args.get("decorative")
    if title is None and description is None and decorative is None:
        raise RuntimeError("Give 'title', 'description', or decorative=true.")

    def pages():
        if ub.is_calc(doc):
            sheets = doc.getSheets()
            for sheet_name in sheets.getElementNames():
                yield sheets.getByName(sheet_name).getDrawPage()
        else:
            yield doc.getDrawPage()
            try:                       # Writer images live in their own bag too
                yield doc.getGraphicObjects()
            except Exception:
                pass

    updated, seen = [], []
    for page in pages():
        for i in range(page.getCount()):
            shape = page.getByIndex(i)
            try:
                shape_name = shape.Name
            except Exception:
                shape_name = ""
            seen.append(shape_name)
            if name and shape_name != name:
                continue
            if title is not None:
                shape.Title = str(title)
            if description is not None:
                shape.Description = str(description)
            if decorative is not None:
                try:
                    shape.Decorative = bool(decorative)
                except Exception:
                    pass       # LibreOffice < 7.5 has no Decorative flag
            updated.append(shape_name)
            if name:
                break
    if name and not updated:
        raise RuntimeError("No image or shape named %r. Present: %s"
                           % (name, ", ".join(x for x in seen if x) or "(none)"))
    return {"updated": updated, "count": len(updated),
            "title": title, "description": description,
            "decorative": decorative}


_CONTENT_CONTROL_KINDS = ("rich_text", "plain_text", "checkbox", "dropdown",
                          "combobox", "date", "picture")


def tool_writer_content_control(args):
    """Insert a Word-compatible content control — the Form ▸ Content Controls
    family. Unlike form controls these live IN the text flow, and can be bound
    to an XML data source."""
    doc = _require_writer()
    kind = str(args.get("kind", "rich_text")).lower()
    if kind not in _CONTENT_CONTROL_KINDS:
        raise RuntimeError("kind must be one of %s"
                           % (_CONTENT_CONTROL_KINDS,))

    control = doc.createInstance("com.sun.star.text.ContentControl")
    if args.get("alias"):
        control.Alias = str(args["alias"])
    if args.get("placeholder"):
        control.PlaceholderDocPart = str(args["placeholder"])
    for flag, prop in (("checkbox", "Checkbox"), ("combobox", "ComboBox"),
                       ("date", "Date"), ("picture", "Picture")):
        if kind == flag or (kind == "dropdown" and prop == "ComboBox"):
            try:
                setattr(control, prop, True)
            except Exception:
                pass
    if kind == "plain_text":
        try:
            control.PlainText = True
        except Exception:
            pass
    if kind == "dropdown":
        try:
            control.DropDown = True
        except Exception:
            pass
    if kind == "checkbox" and args.get("checked") is not None:
        control.Checked = bool(args["checked"])
    if kind == "date" and args.get("date_format"):
        control.DateFormat = str(args["date_format"])
    if args.get("items"):
        # list entries are (display, value) pairs on this model
        try:
            control.ListItems = tuple(
                (_pv("DisplayText", str(x)), _pv("Value", str(x)))
                for x in args["items"])
        except Exception:
            pass
    for arg, prop in (("xpath", "DataBindingXpath"),
                      ("xml_prefixes", "DataBindingPrefixMappings")):
        if args.get(arg):
            try:
                setattr(control, prop, str(args[arg]))
            except Exception:
                pass

    text = doc.getText()
    cursor = text.createTextCursor()
    if args.get("search"):
        found = doc.createSearchDescriptor()
        found.setSearchString(str(args["search"]))
        hit = doc.findFirst(found)
        if hit is None:
            raise RuntimeError("Text %r not found to wrap."
                               % args["search"])
        cursor = text.createTextCursorByRange(hit)
    else:
        cursor.gotoEnd(False)
        if args.get("text"):
            text.insertString(cursor, str(args["text"]), False)
            cursor.goLeft(len(str(args["text"])), True)
    text.insertTextContent(cursor, control, True)
    return {"inserted": kind, "alias": args.get("alias"),
            "bound_to": args.get("xpath")}


# --------------------------------------------------------------------------- #
# Tools — the document lifecycle
#
# Deliberately NOT implemented as phase-gated tool sets. Hiding the export tools
# until an "authoring" phase ends means a user who says "just send me the PDF"
# hits a server that appears not to support it — the exact complaint Nelson MCP
# recorded as issue #24 and cited when they rejected progressive disclosure
# (docs/COMPETITOR-STUDY.md). Everything stays visible; this tool supplies the
# ORDER, and it derives the phase from the document itself rather than storing
# one, so it survives a server restart and cannot go stale.
# --------------------------------------------------------------------------- #

def _lifecycle_facts(doc, ub):
    """What is actually true of this document right now."""
    f = {"kind": _doc_kind(doc), "saved_to": doc.getURL() or None}
    try:
        f["unsaved_changes"] = bool(doc.isModified())
    except Exception:
        f["unsaved_changes"] = None

    props = doc.getDocumentProperties()
    f["title"] = props.Title or None
    f["author"] = props.Author or None
    f["subject"] = props.Subject or None
    f["keywords"] = list(props.Keywords) or []
    f["rights"] = getattr(props, "Rights", "") or None
    try:
        f["language"] = props.Language.Language or None
    except Exception:
        f["language"] = None

    if ub.is_writer(doc):
        text = doc.getText().getString()
        f["characters"] = len(text)
        headings, paragraphs = 0, 0
        enum = doc.getText().createEnumeration()
        while enum.hasMoreElements():
            para = enum.nextElement()
            if not para.supportsService("com.sun.star.text.Paragraph"):
                continue
            paragraphs += 1
            try:
                if para.OutlineLevel > 0 or str(para.ParaStyleName).startswith("Heading"):
                    headings += 1
            except Exception:
                pass
        f["paragraphs"], f["headings"] = paragraphs, headings
        try:
            f["tables"] = doc.getTextTables().getCount()
        except Exception:
            f["tables"] = 0
        try:      # a real index/TOC, not merely "there are headings"
            f["has_toc"] = doc.getDocumentIndexes().getCount() > 0
        except Exception:
            f["has_toc"] = False
        missing = []
        try:
            graphics = doc.getGraphicObjects()
            f["images"] = graphics.getCount()
            for name in graphics.getElementNames():
                obj = graphics.getByName(name)
                if not (getattr(obj, "Description", "") or
                        getattr(obj, "Title", "")):
                    missing.append(name)
        except Exception:
            f["images"] = 0
        f["images_without_alt_text"] = missing
    else:
        sheets = doc.getSheets()
        f["sheets"] = list(sheets.getElementNames())
        cells = 0
        for name in f["sheets"]:
            sheet = sheets.getByName(name)
            addr = _sheet_used_addr(sheet)
            if not _addr_is_empty(sheet, addr):
                cells += ((addr.EndRow - addr.StartRow + 1) *
                          (addr.EndColumn - addr.StartColumn + 1))
        f["used_cells"] = cells
        f["images_without_alt_text"] = []
        # same signal calc_detect_errors reports. getCells() hands back an
        # enumeration access, NOT an index access — getCount() raises, and
        # swallowing that silently reported "no broken formulas" on a sheet
        # full of #DIV/0!.
        broken = 0
        for name in f["sheets"]:
            try:
                cells = sheets.getByName(name).queryFormulaCells(4).getCells()
                enum = cells.createEnumeration()
                while enum.hasMoreElements():
                    enum.nextElement()
                    broken += 1
            except Exception:
                pass
        f["formula_errors"] = broken
    return f


def tool_document_lifecycle(args):
    """Where this document is in its life, and the next thing worth doing."""
    ub = _bridge()
    doc = _select_doc(args) or _current_doc()
    f = _lifecycle_facts(doc, ub)
    writer = f["kind"] == "writer"
    has_content = (f.get("characters", 0) > 40 if writer
                   else f.get("used_cells", 0) > 4)

    setup_done, setup_todo = [], []
    for label, ok, action in (
            ("document title", bool(f["title"]),
             "set_document_properties title='…'"),
            ("document language", bool(f["language"]),
             "set_document_properties language='en-GB' (or 'ar-LY')"),
            ("base typography and margins", bool(f["title"]) and has_content,
             "writer_format_document preset='report'|'essay'|'letter'"
             if writer else "calc_format_table preset='clean'|'report'")):
        (setup_done if ok else setup_todo).append({"item": label, "how": action})

    # (label, satisfied, how, optional) — optional items are reported but never
    # gate the phase, or a document that legitimately wants no table of contents
    # would sit in "authoring" for ever.
    author_done, author_todo = [], []
    if writer:
        checks = (("body text", has_content,
                   "writer_append_text / writer_insert_heading", False),
                  ("headings for structure", f.get("headings", 0) > 0,
                   "writer_insert_heading — also what builds the PDF outline", False),
                  ("a table of contents", f.get("has_toc", False),
                   "writer_insert_toc (optional; needs headings first)", True))
    else:
        checks = (("data in the sheet", has_content,
                   "calc_write_range / calc_import_csv", False),
                  ("a formatted table", has_content, "calc_format_table", True),
                  ("no broken formulas", not f.get("formula_errors"),
                   "calc_detect_errors, then fix what it reports", False))
    for label, ok, action, optional in checks:
        entry = {"item": label, "how": action}
        if optional:
            entry["optional"] = True
        (author_done if ok else author_todo).append(entry)

    close_done, close_todo = [], []
    for label, ok, action in (
            ("author recorded", bool(f["author"]), "set_document_properties author='…'"),
            ("subject / keywords", bool(f["subject"]) or bool(f["keywords"]),
             "set_document_properties subject='…' keywords=[…]"),
            ("licence or rights", bool(f["rights"]),
             "set_document_properties rights='CC BY 4.0'"),
            ("alt text on every image", not f["images_without_alt_text"],
             "set_alt_text name='%s' description='…'"
             % (f["images_without_alt_text"][0] if f["images_without_alt_text"] else "…")),
            ("saved to a file", bool(f["saved_to"]), "save_document path='…'"),
            ("no unsaved changes", f.get("unsaved_changes") is False, "save_document")):
        (close_done if ok else close_todo).append({"item": label, "how": action})

    def blocking(items):
        return [i for i in items if not i.get("optional")]

    if blocking(setup_todo):
        phase, focus = "setup", setup_todo
    elif blocking(author_todo):
        phase, focus = "authoring", author_todo
    else:
        phase, focus = "closing", close_todo

    ask = {
        "setup": "Confirm what this document is for and who it is for, then "
                 "agree a title, a language and a look before writing anything.",
        "authoring": "Work through the content with the user, one section or "
                     "sheet at a time, showing the result and asking what to "
                     "change before moving on.",
        "closing": "Walk the user through finishing: the metadata to record, "
                   "whether any image still needs alt text, where to save, and "
                   "whether they want an accessible or a form-fillable PDF.",
    }[phase]

    return {
        "phase": phase,
        "document": _doc_info(doc),
        "facts": f,
        "next_actions": focus[:3],
        "ask_the_user": ask,
        "checklist": {
            "setup": {"done": setup_done, "todo": setup_todo},
            "authoring": {"done": author_done, "todo": author_todo},
            "closing": {"done": close_done, "todo": close_todo},
        },
        "note": "Phases are advisory and derived from the document, not stored. "
                "Every tool stays callable in every phase — if the user asks to "
                "export during authoring, just export.",
    }


# --------------------------------------------------------------------------- #
# Tools — Impress (presentations)
# Slides are addressed by a 1-BASED index everywhere ("slide 3" = the 3rd slide).
# Placeholders resolve by service where LibreOffice reports it reliably (title,
# body) and by structure for notes. Layout ints and the placeholder model were
# measured on LO 25.2 — see the "Phase 0 findings" in docs/PLAN-IMPRESS-MVP.md.
# --------------------------------------------------------------------------- #

_IMPRESS_LAYOUTS = {          # friendly name -> DrawPage.Layout int (measured)
    "title_subtitle": 0,      # title slide: title + subtitle
    "title_content": 1,       # title + one content/outline box
    "two_content": 3,         # title + two content boxes
    "title_only": 19,
    "blank": 20,
}
_LAYOUT_NAMES = {v: k for k, v in _IMPRESS_LAYOUTS.items()}

_PRES = "com.sun.star.presentation."
_TITLE_SVC = _PRES + "TitleTextShape"
_BODY_SVC = _PRES + "OutlinerShape"          # the content/outline placeholder
_TEXT_SVC = "com.sun.star.drawing.Text"
_PAGE_SVC = "com.sun.star.drawing.PageShape"  # the notes-page slide thumbnail


def _impress_pages():
    return _require_impress().getDrawPages()


def _impress_slide(pages, one_based):
    n = pages.getCount()
    try:
        i = int(one_based) - 1
    except (TypeError, ValueError):
        raise RuntimeError("slide must be a 1-based number, got: %r" % (one_based,))
    if i < 0 or i >= n:
        raise RuntimeError("slide %r is out of range 1..%d" % (one_based, n))
    return pages.getByIndex(i)


def _layout_name(page):
    return _LAYOUT_NAMES.get(page.Layout, "custom(%d)" % page.Layout)


def _shape_by_service(page, svc):
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        try:
            if shp.supportsService(svc):
                return shp
        except Exception:
            pass
    return None


def _ph_title(page):
    return _shape_by_service(page, _TITLE_SVC)


def _is_placeholder(shp):
    """True for a layout placeholder (title/body/subtitle), False for an inserted
    shape. Both carry the generic presentation.Shape service on a slide, so that
    cannot tell them apart; IsPlaceholderDependent can (Phase-0 finding)."""
    try:
        return bool(shp.IsPlaceholderDependent)
    except Exception:
        return False


def _ph_body(page):
    """The content/outline placeholder. Falls back to the title-slide subtitle,
    which reports no specific text-shape service — but only among real layout
    placeholders, so an inserted text box is never mistaken for the body."""
    body = _shape_by_service(page, _BODY_SVC)
    if body is not None:
        return body
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        if (_is_placeholder(shp)
                and shp.supportsService(_TEXT_SVC)
                and not shp.supportsService(_TITLE_SVC)):
            return shp
    return None


def _ph_notes(page):
    """The notes text box on the slide's notes page: the text-bearing shape that
    is not the slide-thumbnail PageShape (Phase-0 finding)."""
    if not hasattr(page, "getNotesPage"):
        return None
    notes = page.getNotesPage()
    for i in range(notes.getCount()):
        shp = notes.getByIndex(i)
        if shp.supportsService(_TEXT_SVC) and not shp.supportsService(_PAGE_SVC):
            return shp
    return None


def tool_impress_add_slide(args):
    # insertNewByIndex(n) inserts the new page AFTER 0-based index n (measured),
    # so 'after' (1-based) maps straight to it; omitting 'after' appends. There
    # is no reorder API, so there is no "insert at the very front" — build a deck
    # front-to-back. page.Number reports the resulting 1-based position.
    pages = _impress_pages()
    count = pages.getCount()
    after = args.get("after")
    if after is None:
        idx = max(0, count - 1)
    else:
        a = int(after)
        if a < 1 or a > count:
            raise RuntimeError("after=%r is out of range 1..%d" % (after, count))
        idx = a - 1
    layout = args.get("layout", "title_content")
    if layout not in _IMPRESS_LAYOUTS:
        raise RuntimeError("unknown layout %r; choose one of %s"
                           % (layout, sorted(_IMPRESS_LAYOUTS)))
    page = pages.insertNewByIndex(idx)
    page.Layout = _IMPRESS_LAYOUTS[layout]
    return {"slide": page.Number, "count": pages.getCount(), "layout": layout}


def tool_impress_overview(args):
    pages = _impress_pages()
    slides = []
    for i in range(pages.getCount()):
        page = pages.getByIndex(i)
        title = _ph_title(page)
        body = _ph_body(page)
        notes = _ph_notes(page)
        slides.append({
            "index": i + 1,
            "layout": _layout_name(page),
            "title": title.getString() if title else "",
            "text_len": len(body.getString()) if body else 0,
            "has_notes": bool(notes and notes.getString().strip()),
        })
    return {"count": pages.getCount(), "slides": slides}


def _bullet_parts(b):
    """A bullet is either a plain string or {'text':..., 'level':...}."""
    if isinstance(b, str):
        return b, 0
    return str(b.get("text", "")), int(b.get("level", 0) or 0)


def tool_impress_set_title(args):
    page = _impress_slide(_impress_pages(), args["slide"])
    shp = _ph_title(page)
    if shp is None:
        raise RuntimeError("slide %s has no title placeholder; give it a layout "
                           "with a title (e.g. title_content)" % args["slide"])
    shp.setString(str(args["text"]))
    return {"slide": int(args["slide"]), "title": args["text"]}


def tool_impress_set_content(args):
    from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
    page = _impress_slide(_impress_pages(), args["slide"])
    body = _ph_body(page)
    if body is None:
        raise RuntimeError("slide %s has no content placeholder; use a layout "
                           "like 'title_content' or 'two_content'" % args["slide"])
    bullets = args.get("bullets") or []
    text = body.getText()
    text.setString("")
    cursor = text.createTextCursor()
    for i, b in enumerate(bullets):
        txt, _ = _bullet_parts(b)
        if i:
            # collapseToEnd after each insert, or multi-line inserts reverse
            text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
            cursor.collapseToEnd()
        text.insertString(cursor, txt, False)
        cursor.collapseToEnd()
    for b, para in zip(bullets, text.createEnumeration()):
        _, lvl = _bullet_parts(b)
        if lvl:                       # default level (0) reads back as None; leave it
            try:
                para.NumberingLevel = lvl
            except Exception:
                pass
    return {"slide": int(args["slide"]), "bullets": len(bullets)}


def tool_impress_read_slide(args):
    page = _impress_slide(_impress_pages(), args["slide"])
    title = _ph_title(page)
    body = _ph_body(page)
    bullets = []
    if body is not None:
        for para in body.getText().createEnumeration():
            lvl = getattr(para, "NumberingLevel", 0)
            bullets.append({"text": para.getString(),
                            "level": int(lvl) if lvl else 0})
    shapes = []
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        if _is_placeholder(shp):   # a layout placeholder, not inserted content
            continue
        shapes.append(shp.Name or ("shape#%d" % i))
    notes = _ph_notes(page)
    return {"index": int(args["slide"]),
            "layout": _layout_name(page),
            "title": title.getString() if title else "",
            "bullets": bullets,
            "shapes": shapes,
            "notes": notes.getString() if notes else ""}


def tool_impress_set_notes(args):
    page = _impress_slide(_impress_pages(), args["slide"])
    shp = _ph_notes(page)
    if shp is None:
        raise RuntimeError("slide %s has no speaker-notes area" % args["slide"])
    shp.setString(str(args["text"]))
    return {"slide": int(args["slide"]), "notes": args["text"]}


def _place_shape(shape, args, dx=10, dy=10, dw=40, dh=30):
    """Size + position an added shape from mm args (1/100 mm on the wire)."""
    size = _uno_struct("com.sun.star.awt.Size")
    size.Width = _mm100(args.get("width_mm", dw))
    size.Height = _mm100(args.get("height_mm", dh))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
    pos.X = _mm100(args.get("x_mm", dx))
    pos.Y = _mm100(args.get("y_mm", dy))
    shape.setPosition(pos)


def tool_impress_insert_shape(args):
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    kind = str(args.get("kind", "rectangle")).lower()
    service = _DRAW_SHAPES.get(kind)
    if not service:
        raise RuntimeError("kind must be one of %s" % sorted(_DRAW_SHAPES))
    shape = doc.createInstance(service)
    page.add(shape)
    _place_shape(shape, args)
    if args.get("fill_color") is not None:
        try:
            shape.FillColor = _hex_color(args["fill_color"])
        except Exception:
            pass
    if args.get("text"):
        shape.setString(str(args["text"]))
    return {"slide": int(args["slide"]), "kind": kind,
            "name": getattr(shape, "Name", "")}


def tool_impress_insert_text_box(args):
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    shape = doc.createInstance("com.sun.star.drawing.TextShape")
    page.add(shape)
    _place_shape(shape, args, dw=80, dh=20)
    try:
        shape.TextAutoGrowHeight = True
    except Exception:
        pass
    shape.setString(str(args.get("text", "")))
    return {"slide": int(args["slide"]), "name": getattr(shape, "Name", "")}


def tool_impress_insert_image(args):
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("Image file not found: %s" % path)
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    state = _connect()
    provider = state["smgr"].createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", state["ctx"])
    graphic = provider.queryGraphic((_pv("URL", _to_url(path)),))
    if graphic is None:
        raise RuntimeError("Could not load image: %s" % path)
    shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
    shape.Graphic = graphic
    page.add(shape)
    size = _uno_struct("com.sun.star.awt.Size")
    try:
        native = graphic.Size100thMM
        size.Width = (_mm100(args["width_mm"]) if args.get("width_mm")
                      else native.Width or 6000)
        size.Height = (_mm100(args["height_mm"]) if args.get("height_mm")
                       else native.Height or 4000)
    except Exception:
        size.Width = _mm100(args.get("width_mm", 60))
        size.Height = _mm100(args.get("height_mm", 40))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
    pos.X = _mm100(args.get("x_mm", 10))
    pos.Y = _mm100(args.get("y_mm", 10))
    shape.setPosition(pos)
    return {"slide": int(args["slide"]), "inserted": os.path.basename(path),
            "name": getattr(shape, "Name", "")}


def tool_impress_set_layout(args):
    page = _impress_slide(_impress_pages(), args["slide"])
    layout = args["layout"]
    if layout not in _IMPRESS_LAYOUTS:
        raise RuntimeError("unknown layout %r; choose one of %s"
                           % (layout, sorted(_IMPRESS_LAYOUTS)))
    page.Layout = _IMPRESS_LAYOUTS[layout]
    return {"slide": int(args["slide"]), "layout": layout}


def tool_impress_delete_slide(args):
    pages = _impress_pages()
    if pages.getCount() <= 1:
        raise RuntimeError("cannot delete the only slide in a presentation")
    page = _impress_slide(pages, args["slide"])
    pages.remove(page)
    return {"deleted": int(args["slide"]), "count": pages.getCount()}


def tool_impress_duplicate_slide(args):
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    doc.duplicate(page)   # XDrawPageDuplicator: inserts the copy right after
    return {"slide": int(args["slide"]) + 1,
            "count": doc.getDrawPages().getCount()}


# friendly name -> (TransitionType, TransitionSubType) SMIL constant names,
# resolved at runtime via uno.getConstantByName so no fragile ints are hardcoded
_IMPRESS_TRANSITIONS = {
    "none": None,
    "fade": ("FADE", "CROSSFADE"),
    "wipe": ("BARWIPE", "LEFTTORIGHT"),
    "push": ("PUSHWIPE", "FROMRIGHT"),
    "cover": ("SLIDEWIPE", "FROMRIGHT"),
    "uncover": ("SLIDEWIPE", "FROMLEFT"),
    "dissolve": ("DISSOLVE", "DEFAULT"),
    "wheel": ("PINWHEELWIPE", "ONEBLADE"),
    "cut": ("BARWIPE", "LEFTTORIGHT"),
}


def _impress_target_slides(args):
    """Slides to act on: every slide when 'all' is true, else the one 'slide'."""
    pages = _impress_pages()
    if args.get("all"):
        return [pages.getByIndex(i) for i in range(pages.getCount())]
    return [_impress_slide(pages, args["slide"])]


def tool_impress_set_transition(args):
    import uno
    name = str(args.get("type", "fade")).lower()
    if name not in _IMPRESS_TRANSITIONS:
        raise RuntimeError("type must be one of %s" % sorted(_IMPRESS_TRANSITIONS))
    pair = _IMPRESS_TRANSITIONS[name]
    advance = args.get("advance_secs")   # None -> on click; number -> auto after N s
    pages = _impress_target_slides(args)
    for page in pages:
        if pair is None:
            page.TransitionType = 0
        else:
            page.TransitionType = uno.getConstantByName(
                "com.sun.star.animations.TransitionType." + pair[0])
            page.TransitionSubtype = uno.getConstantByName(
                "com.sun.star.animations.TransitionSubType." + pair[1])
        if args.get("duration") is not None:
            page.TransitionDuration = float(args["duration"])
        if advance is None:
            page.Change = 0                       # advance on click
        else:
            page.Change = 1                       # automatic
            page.Duration = int(advance)          # seconds to wait
    return {"slides": len(pages), "type": name}


_SLIDE_IMG_FILTERS = {"png": "image/png", "svg": "image/svg+xml",
                      "jpg": "image/jpeg", "jpeg": "image/jpeg"}


def tool_impress_export_slides(args):
    fmt = str(args.get("format", "png")).lower()
    media = _SLIDE_IMG_FILTERS.get(fmt)
    if media is None:
        raise RuntimeError("format must be one of %s" % sorted(_SLIDE_IMG_FILTERS))
    out_dir = args["dir"]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    pages = _impress_target_slides(args) if (args.get("all") or args.get("slide")) \
        else [p for p in _impress_target_slides({"all": True})]
    state = _connect()
    gef = state["smgr"].createInstanceWithContext(
        "com.sun.star.drawing.GraphicExportFilter", state["ctx"])
    written = []
    all_pages = _impress_pages()
    for page in pages:
        n = page.Number
        path = os.path.join(out_dir, "slide-%02d.%s" % (n, fmt))
        gef.setSourceDocument(page)
        gef.filter((_pv("URL", _to_url(path)), _pv("MediaType", media)))
        written.append(os.path.abspath(path))
    return {"format": fmt, "count": len(written), "files": written}


_CHART_CLSID = "12DCAE26-281F-416F-A234-C3086127382E"
_CHART_DIAGRAMS = {
    "column": ("BarDiagram", True), "bar": ("BarDiagram", False),
    "line": ("LineDiagram", None), "area": ("AreaDiagram", None),
    "pie": ("PieDiagram", None),
}


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def tool_impress_insert_chart(args):
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    kind = str(args.get("chart_type", "column")).lower()
    if kind not in _CHART_DIAGRAMS:
        raise RuntimeError("chart_type must be one of %s" % sorted(_CHART_DIAGRAMS))
    ole = doc.createInstance("com.sun.star.presentation.OLE2Shape")
    page.add(ole)
    _place_shape(ole, args, dx=20, dy=40, dw=160, dh=100)
    ole.CLSID = _CHART_CLSID
    model = ole.Model
    svc, vertical = _CHART_DIAGRAMS[kind]
    model.setDiagram(model.createInstance("com.sun.star.chart." + svc))
    if vertical is not None:
        try:
            model.Diagram.Vertical = vertical
        except Exception:
            pass
    data = args.get("data")
    if data and len(data) >= 2:
        # row 0 = column/series headers (skip the corner cell); col 0 = row labels
        col_desc = tuple(str(x) for x in data[0][1:])
        row_desc = tuple(str(r[0]) for r in data[1:])
        matrix = tuple(tuple(_to_float(v) for v in r[1:]) for r in data[1:])
        xd = model.getData()
        xd.setData(matrix)
        xd.setColumnDescriptions(col_desc)
        xd.setRowDescriptions(row_desc)
    if args.get("title"):
        try:
            model.HasMainTitle = True
            model.Title.String = str(args["title"])
        except Exception:
            pass
    return {"slide": int(args["slide"]), "chart_type": kind,
            "name": getattr(ole, "Name", "")}


def tool_impress_insert_table(args):
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    rows = int(args.get("rows", 0))
    cols = int(args.get("cols", 0))
    data = args.get("data")
    if data:
        rows = rows or len(data)
        cols = cols or max((len(r) for r in data), default=0)
    if rows < 1 or cols < 1:
        raise RuntimeError("need rows>=1 and cols>=1 (or a non-empty 'data' grid)")
    shape = doc.createInstance("com.sun.star.drawing.TableShape")
    page.add(shape)
    _place_shape(shape, args, dx=20, dy=40, dw=200, dh=80)
    model = shape.Model
    # a fresh table model is 1x1 — grow to the requested size
    r_have, c_have = model.RowCount, model.ColumnCount
    if rows > r_have:
        model.Rows.insertByIndex(r_have, rows - r_have)
    if cols > c_have:
        model.Columns.insertByIndex(c_have, cols - c_have)
    if data:
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                if r < rows and c < cols:
                    model.getCellByPosition(c, r).setString(str(val))
    return {"slide": int(args["slide"]), "rows": rows, "cols": cols,
            "name": getattr(shape, "Name", "")}


# --------------------------------------------------------------------------- #
# Tools — Draw (vector drawings). A separate surface from Impress, but the shape/
# text/image primitives are the same drawing model (_DRAW_SHAPES, _place_shape,
# GraphicProvider). Pages are addressed by a 1-based index.
# --------------------------------------------------------------------------- #

def tool_impress_slideshow(args):
    """Control the on-screen slideshow. start() launches the presentation in the
    LibreOffice window — it needs a GUI session (a headless office has no display),
    so this is for driving a LibreOffice the user actually has open."""
    doc = _require_impress()
    pres = doc.Presentation
    action = str(args.get("action", "status")).lower()
    if action == "start":
        if args.get("from_slide"):
            try:
                pres.FirstPage = int(args["from_slide"])
            except Exception:
                pass
        pres.start()
    elif action in ("stop", "end"):
        pres.end()
    elif action != "status":
        raise RuntimeError("action must be start, stop, or status")
    running = False
    try:
        running = bool(pres.isRunning())
    except Exception:
        pass
    return {"action": action, "running": running}


_BG_SHAPE_NAME = "__mcp_background__"


def tool_impress_set_background(args):
    """Set a slide background — a solid 'color' (hex), an 'image' (local file
    stretched to fill), or both — on one slide or every slide ('all':true), with
    an optional 'transparency' (0 opaque .. 100 invisible). Implemented as a
    full-slide filled rectangle sent to the back: LO 25.2 exposes no working
    DrawPage.Background fill (verified by rendering), and this renders identically.
    Idempotent: replaces its own prior background rectangle rather than stacking."""
    from com.sun.star.drawing.FillStyle import SOLID, BITMAP
    from com.sun.star.drawing.LineStyle import NONE as LINE_NONE
    doc = _require_impress()
    color = args.get("color")
    image = args.get("image")
    if not color and not image:
        raise RuntimeError("give 'color' (hex like '#2E4053') and/or 'image' (file path)")
    graphic = None
    if image:
        if not os.path.exists(image):
            raise RuntimeError("Image file not found: %s" % image)
        state = _connect()
        gp = state["smgr"].createInstanceWithContext(
            "com.sun.star.graphic.GraphicProvider", state["ctx"])
        graphic = gp.queryGraphic((_pv("URL", _to_url(image)),))
        if graphic is None:
            raise RuntimeError("Could not load image: %s" % image)
    transp = args.get("transparency")
    pages = _impress_target_slides(args)
    for page in pages:
        for i in range(page.getCount()):
            shp = page.getByIndex(i)
            if getattr(shp, "Name", "") == _BG_SHAPE_NAME:
                page.remove(shp)
                break
        rect = doc.createInstance("com.sun.star.drawing.RectangleShape")
        page.add(rect)
        pos = _uno_struct("com.sun.star.awt.Point"); pos.X = 0; pos.Y = 0
        siz = _uno_struct("com.sun.star.awt.Size")
        siz.Width = page.Width; siz.Height = page.Height
        rect.setPosition(pos); rect.setSize(siz)
        try:
            rect.LineStyle = LINE_NONE
        except Exception:
            pass
        if image:
            from com.sun.star.drawing.BitmapMode import STRETCH
            rect.FillStyle = BITMAP
            rect.FillBitmap = graphic          # XGraphic accepted by pyuno here
            try:
                rect.FillBitmapMode = STRETCH
            except Exception:
                pass
        else:
            rect.FillStyle = SOLID
            rect.FillColor = _hex_color(color)
        if transp is not None:
            try:
                rect.FillTransparence = max(0, min(100, int(transp)))
            except Exception:
                pass
        rect.Name = _BG_SHAPE_NAME
        try:
            rect.ZOrder = 0          # send behind the slide content
        except Exception:
            pass
    return {"slides": len(pages), "color": color,
            "image": os.path.basename(image) if image else None,
            "transparency": transp}


def _draw_page(pages, one_based):
    n = pages.getCount()
    try:
        i = int(one_based) - 1
    except (TypeError, ValueError):
        raise RuntimeError("page must be a 1-based number, got: %r" % (one_based,))
    if i < 0 or i >= n:
        raise RuntimeError("page %r is out of range 1..%d" % (one_based, n))
    return pages.getByIndex(i)


def _shape_kind(shp):
    for s in shp.SupportedServiceNames:
        if s.startswith("com.sun.star.drawing.") and s.endswith("Shape"):
            return s.rsplit(".", 1)[-1]
    return "Shape"


def tool_draw_overview(args):
    pages = _require_draw().getDrawPages()
    out = [{"index": i + 1, "name": getattr(pages.getByIndex(i), "Name", ""),
            "shapes": pages.getByIndex(i).getCount()}
           for i in range(pages.getCount())]
    return {"count": pages.getCount(), "pages": out}


def tool_draw_read_page(args):
    page = _draw_page(_require_draw().getDrawPages(), args["page"])
    shapes = []
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        text = ""
        try:
            text = shp.getString()
        except Exception:
            pass
        shapes.append({"index": i + 1, "name": getattr(shp, "Name", ""),
                       "kind": _shape_kind(shp), "text": text})
    return {"page": int(args["page"]), "name": getattr(page, "Name", ""),
            "shapes": shapes}


def tool_draw_add_page(args):
    pages = _require_draw().getDrawPages()
    pages.insertNewByIndex(max(0, pages.getCount() - 1))   # append
    page = pages.getByIndex(pages.getCount() - 1)
    if args.get("name"):
        try:
            page.Name = str(args["name"])
        except Exception:
            pass
    return {"count": pages.getCount(), "page": page.Number}


def tool_draw_insert_shape(args):
    doc = _require_draw()
    page = _draw_page(doc.getDrawPages(), args.get("page", 1))
    kind = str(args.get("kind", "rectangle")).lower()
    service = _DRAW_SHAPES.get(kind)
    if not service:
        raise RuntimeError("kind must be one of %s" % sorted(_DRAW_SHAPES))
    shape = doc.createInstance(service)
    page.add(shape)
    _place_shape(shape, args)
    if args.get("fill_color") is not None:
        try:
            shape.FillColor = _hex_color(args["fill_color"])
        except Exception:
            pass
    if args.get("text"):
        shape.setString(str(args["text"]))
    return {"page": args.get("page", 1), "kind": kind,
            "name": getattr(shape, "Name", "")}


def tool_draw_insert_text_box(args):
    doc = _require_draw()
    page = _draw_page(doc.getDrawPages(), args.get("page", 1))
    shape = doc.createInstance("com.sun.star.drawing.TextShape")
    page.add(shape)
    _place_shape(shape, args, dw=80, dh=20)
    try:
        shape.TextAutoGrowHeight = True
    except Exception:
        pass
    shape.setString(str(args.get("text", "")))
    return {"page": args.get("page", 1), "name": getattr(shape, "Name", "")}


def tool_draw_insert_image(args):
    path = args["path"]
    if not os.path.exists(path):
        raise RuntimeError("Image file not found: %s" % path)
    doc = _require_draw()
    page = _draw_page(doc.getDrawPages(), args.get("page", 1))
    state = _connect()
    provider = state["smgr"].createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", state["ctx"])
    graphic = provider.queryGraphic((_pv("URL", _to_url(path)),))
    if graphic is None:
        raise RuntimeError("Could not load image: %s" % path)
    shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
    shape.Graphic = graphic
    page.add(shape)
    size = _uno_struct("com.sun.star.awt.Size")
    try:
        native = graphic.Size100thMM
        size.Width = (_mm100(args["width_mm"]) if args.get("width_mm")
                      else native.Width or 6000)
        size.Height = (_mm100(args["height_mm"]) if args.get("height_mm")
                       else native.Height or 4000)
    except Exception:
        size.Width = _mm100(args.get("width_mm", 60))
        size.Height = _mm100(args.get("height_mm", 40))
    shape.setSize(size)
    pos = _uno_struct("com.sun.star.awt.Point")
    pos.X = _mm100(args.get("x_mm", 10))
    pos.Y = _mm100(args.get("y_mm", 10))
    shape.setPosition(pos)
    return {"page": args.get("page", 1), "inserted": os.path.basename(path),
            "name": getattr(shape, "Name", "")}


def tool_draw_insert_connector(args):
    doc = _require_draw()
    pages = doc.getDrawPages()
    page = _draw_page(pages, args.get("page", 1))
    conn = doc.createInstance("com.sun.star.drawing.ConnectorShape")
    page.add(conn)
    sp = _uno_struct("com.sun.star.awt.Point")
    sp.X = _mm100(args.get("x1_mm", 10)); sp.Y = _mm100(args.get("y1_mm", 10))
    ep = _uno_struct("com.sun.star.awt.Point")
    ep.X = _mm100(args.get("x2_mm", 80)); ep.Y = _mm100(args.get("y2_mm", 60))
    conn.StartPosition = sp
    conn.EndPosition = ep
    # optionally glue the ends to shapes on the page (1-based shape index)
    for arg, prop in (("start_shape", "StartShape"), ("end_shape", "EndShape")):
        if args.get(arg) is not None:
            idx = int(args[arg]) - 1
            if 0 <= idx < page.getCount():
                try:
                    setattr(conn, prop, page.getByIndex(idx))
                except Exception:
                    pass
    return {"page": args.get("page", 1), "name": getattr(conn, "Name", "")}


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
    # upstream-parity: document ops, macros, dispatcher, calc convenience
    "run_python_macro": tool_run_python_macro,
    "list_macros": tool_list_macros,
    "convert": tool_convert,
    "merge": tool_merge,
    "list_templates": tool_list_templates,
    "create_from_template": tool_create_from_template,
    "dispatch": tool_dispatch,
    "calc_statistics": tool_calc_statistics,
    "read_spreadsheet": tool_read_spreadsheet,
    # everyday composites
    "calc_overview": tool_calc_overview,
    "calc_format_table": tool_calc_format_table,
    "calc_clean_data": tool_calc_clean_data,
    "writer_format_document": tool_writer_format_document,
    # everyday tools borrowed from the sibling projects
    "calc_import_csv": tool_calc_import_csv,
    "calc_detect_errors": tool_calc_detect_errors,
    "list_recent_documents": tool_list_recent_documents,
    "print_document": tool_print_document,
    "writer_resolve_comment": tool_writer_resolve_comment,
    "writer_captions": tool_writer_captions,
    # failure recovery
    "lo_health": tool_lo_health,
    "lo_recover": tool_lo_recover,
    "checkpoint_document": tool_checkpoint_document,
    "document_watch": tool_document_watch,
    # lifecycle
    "document_lifecycle": tool_document_lifecycle,
    # print setup, accessibility, content controls
    "print_settings": tool_print_settings,
    "set_alt_text": tool_set_alt_text,
    "writer_content_control": tool_writer_content_control,
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
    # impress (presentations)
    "impress_overview": tool_impress_overview,
    "impress_read_slide": tool_impress_read_slide,
    "impress_add_slide": tool_impress_add_slide,
    "impress_set_title": tool_impress_set_title,
    "impress_set_content": tool_impress_set_content,
    "impress_set_notes": tool_impress_set_notes,
    "impress_insert_image": tool_impress_insert_image,
    "impress_insert_shape": tool_impress_insert_shape,
    "impress_insert_text_box": tool_impress_insert_text_box,
    "impress_set_layout": tool_impress_set_layout,
    "impress_delete_slide": tool_impress_delete_slide,
    "impress_duplicate_slide": tool_impress_duplicate_slide,
    "impress_set_transition": tool_impress_set_transition,
    "impress_export_slides": tool_impress_export_slides,
    "impress_insert_table": tool_impress_insert_table,
    "impress_insert_chart": tool_impress_insert_chart,
    "impress_slideshow": tool_impress_slideshow,
    "impress_set_background": tool_impress_set_background,
    # draw (vector drawings)
    "draw_overview": tool_draw_overview,
    "draw_read_page": tool_draw_read_page,
    "draw_add_page": tool_draw_add_page,
    "draw_insert_shape": tool_draw_insert_shape,
    "draw_insert_text_box": tool_draw_insert_text_box,
    "draw_insert_image": tool_draw_insert_image,
    "draw_insert_connector": tool_draw_insert_connector,
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
     "description": "Create and open a new empty document ('calc' spreadsheet, 'writer' text document, 'impress' presentation, or 'draw' drawing).",
     "inputSchema": _schema({"type": dict(_STR, enum=["calc", "writer", "impress", "draw"])}, ["type"])},
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
                             "number_format": dict(_STR, description="raw LibreOffice number format code, e.g. '#,##0.00'"),
                             "number_preset": dict(_STR, enum=["number", "currency", "percent", "date", "time", "datetime", "text"],
                                                   description="named format resolved FOR A LOCALE — prefer this over number_format for money and dates, it gets the currency symbol, digit grouping and date order right per country"),
                             "locale": dict(_STR, description="BCP-47 tag for number_preset, e.g. 'en-US', 'ar-LY', 'de-DE'; omit for the document's own locale"),
                             "decimals": dict(_INT, description="decimal places for number_preset"),
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
     "description": "Find & replace text across the Writer document. Keeps the formatting of what it replaced: a match spanning several formatting runs (part bold, part not) would otherwise come back chopped along the OLD run boundaries — the replacement now takes the formatting of the match's first character. Set preserve_formatting=false for LibreOffice's raw behaviour. With regex=true, 'search' is an ICU regular expression and $1..$n backreferences work in 'replace'.",
     "inputSchema": _schema({"search": _STR, "replace": _STR,
                             "match_case": _BOOL, "whole_words": _BOOL,
                             "regex": dict(_BOOL, description="treat 'search' as a regular expression"),
                             "preserve_formatting": dict(_BOOL, description="default true")},
                            ["search"])},
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
     "description": "Insert a form control into the active Calc sheet or Writer document — the whole Form menu. Position and size in mm. For a button, 'url' opens a URL/dispatch command when clicked; listbox/combobox take 'items'; the numeric family (numeric, currency, formatted, date, time) takes value/min/max/decimals. 'required' and 'readonly' apply wherever the control supports them. Export with export_document form_fields=true to turn these into fillable PDF fields. (Image Control and Table Control are database-bound and need a data source, so they are not offered here.)",
     "inputSchema": _schema({"kind": dict(_STR, enum=sorted(_FORM_COMPONENTS)),
                             "label": dict(_STR, description="caption (button/checkbox/radio/groupbox/label)"),
                             "text": dict(_STR, description="default text (textfield)"),
                             "items": {"type": "array", "items": _STR, "description": "entries (listbox/combobox)"},
                             "url": dict(_STR, description="button target URL / dispatch command"),
                             "image": dict(_STR, description="picture file path (imagebutton)"),
                             "name": dict(_STR, description="control name"),
                             "value": dict(_NUM, description="default value (numeric family)"),
                             "min": dict(_NUM, description="minimum (numeric family)"),
                             "max": dict(_NUM, description="maximum (numeric family)"),
                             "decimals": dict(_INT, description="decimal places (numeric/currency)"),
                             "currency": dict(_STR, description="currency symbol"),
                             "format": dict(_STR, description="edit mask (pattern control)"),
                             "required": dict(_BOOL, description="must be filled in"),
                             "readonly": _BOOL,
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
     "description": "Store to a path with filter options. format 'pdf' or 'csv'; defaults to the path extension. PDF supports archival (pdfa), ACCESSIBILITY (tagged, pdfua — pair these with set_alt_text or the pictures stay silent), FILLABLE FORMS (form_fields turns Writer form controls into real AcroForm fields a browser can fill and save), and two separate passwords: 'password' locks opening, 'owner_password' restricts what a reader may do (can_print / can_modify / can_copy / can_annotate).",
     "inputSchema": _schema({"path": _STR,
                             "format": dict(_STR, enum=["pdf", "csv"]),
                             "page_range": dict(_STR, description="PDF pages, e.g. '1-3'"),
                             "pdfa": dict(_BOOL, description="PDF/A-1 archival"),
                             "tagged": dict(_BOOL, description="tagged PDF — the basis of accessibility"),
                             "pdfua": dict(_BOOL, description="PDF/UA-1 accessibility compliance (implies tagged)"),
                             "bookmarks": dict(_BOOL, description="export headings as PDF bookmarks"),
                             "form_fields": dict(_BOOL, description="export form controls as fillable PDF fields"),
                             "forms_type": dict(_INT, description="0=FDF 1=PDF/AcroForm (default) 2=HTML 3=XML"),
                             "watermark": dict(_STR, description="draw this text across every page"),
                             "quality": dict(_INT, description="PDF image quality 0-100"),
                             "password": dict(_STR, description="PDF open password"),
                             "owner_password": dict(_STR, description="permissions password — restricts what a reader may do"),
                             "can_print": _BOOL, "can_modify": _BOOL,
                             "can_copy": _BOOL, "can_annotate": _BOOL,
                             "delimiter": dict(_STR, description="CSV field delimiter (default ',')"),
                             "quote": dict(_STR, description="CSV text delimiter (default '\"')")},
                            ["path"])},
    {"name": "set_document_properties",
     "description": "Set document metadata — everything in File > Properties > Description, including the Dublin Core fields. title/author/subject/description plus coverage/identifier/rights/source/type (single values) and keywords/contributor/publisher/relation (ARRAYS — these are multi-value in ODF). 'language' is a BCP-47 tag ('ar-LY') and sets the document language a screen reader announces. 'custom' holds user-defined properties ({name: value}; null removes). Note: a PDF's own info panel only carries title/author/subject/keywords — the rest survive in ODF, and in PDF/A's XMP.",
     "inputSchema": _schema({"title": _STR, "author": _STR, "subject": _STR,
                             "description": dict(_STR, description="the 'Comments' box"),
                             "keywords": {"type": "array", "items": _STR},
                             "contributor": {"type": "array", "items": _STR},
                             "publisher": {"type": "array", "items": _STR},
                             "relation": {"type": "array", "items": _STR},
                             "coverage": _STR, "identifier": _STR,
                             "rights": dict(_STR, description="licence / copyright, e.g. 'CC BY 4.0'"),
                             "source": _STR,
                             "type": dict(_STR, description="Dublin Core resource type, e.g. 'Text'"),
                             "language": dict(_STR, description="BCP-47 document language, e.g. 'en-GB' or 'ar-LY'"),
                             "custom": {"type": "object", "description": "user-defined props"}})},
    {"name": "document_lifecycle",
     "description": "START HERE for any document work. Reads the open document and reports which phase it is in — SETUP (title, language, house style), AUTHORING (content, headings, tables), or CLOSING (metadata, alt text, save, export) — with what is already done, what is left, and the exact tool for each remaining step. Also returns 'ask_the_user': what to ask before proceeding. Call it again after finishing a stage, or whenever you are unsure what to do next. Phases are ADVISORY and derived from the document itself, never stored: every tool works in every phase, so if the user asks to export mid-way, just export.",
     "inputSchema": _schema({"title": dict(_STR, description="match the document by window-title substring"),
                             "url": dict(_STR, description="match the document by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
    {"name": "print_settings",
     "description": "Read or change how a document prints: printer name, paper size, orientation, and the per-application content switches. Writer exposes PrintGraphics/PrintTables/PrintDrawings/PrintControls/PrintPageBackground/PrintBlackFonts/PrintEmptyPages/PrintHiddenText/PrintLeftPages/PrintRightPages/PrintReversed/PrintProspect (booklet)/PrintProspectRTL/...; Calc exposes PrintGrid/PrintHeaders/PrintCharts/PrintObjects/PrintFormulas/PrintNotes/PrintZeroValues/PrintDownFirst/... Call with no arguments to read the current state and the list valid for this document.",
     "inputSchema": _schema({"printer": dict(_STR, description="printer name"),
                             "paper": dict(_STR, enum=list(_PAPER_FORMATS)),
                             "orientation": dict(_STR, enum=["portrait", "landscape"]),
                             "options": {"type": "object", "description": "{PrintOptionName: true|false} — names must match the application's own list"},
                             "title": _STR, "url": _STR, "index": _INT})},
    {"name": "set_alt_text",
     "description": "Give an image or shape alternative text — the 'Alt Text' a screen reader announces, and what makes a tagged PDF genuinely accessible instead of merely structured. Set 'name' to target one object (writer_list_figures / calc_list_shapes give the names), or omit it to apply to every image and shape. decorative=true marks it as ornamental so assistive tech skips it.",
     "inputSchema": _schema({"name": dict(_STR, description="object name; omit to apply to all"),
                             "title": dict(_STR, description="short label"),
                             "description": dict(_STR, description="the longer alt text"),
                             "decorative": dict(_BOOL, description="purely ornamental — skipped by screen readers"),
                             "index": _INT, "url": _STR})},
    {"name": "writer_content_control",
     "description": "Insert a Word-compatible content control (Form > Content Controls): rich_text, plain_text, checkbox, dropdown, combobox, date or picture. Unlike form controls these sit IN the text flow rather than floating over it, survive round-tripping to .docx, and can be bound to XML data via 'xpath'. Wrap existing text with 'search', or append with 'text'.",
     "inputSchema": _schema({"kind": dict(_STR, enum=list(_CONTENT_CONTROL_KINDS)),
                             "text": dict(_STR, description="text to insert and wrap"),
                             "search": dict(_STR, description="wrap the first occurrence of this instead"),
                             "alias": dict(_STR, description="the control's title/name"),
                             "placeholder": _STR,
                             "items": {"type": "array", "items": _STR, "description": "dropdown/combobox entries"},
                             "checked": dict(_BOOL, description="checkbox initial state"),
                             "date_format": dict(_STR, description="e.g. 'YYYY-MM-DD'"),
                             "xpath": dict(_STR, description="XML data binding XPath"),
                             "xml_prefixes": dict(_STR, description="namespace prefix mappings for xpath")},
                            ["kind"])},
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
     "description": "Execute an arbitrary .uno: command against the active frame. This is the widest escape hatch there is: EVERY menu item and toolbar button in LibreOffice is a .uno: command, including many with no model-level API at all — so when no dedicated tool fits, this usually still can. Examples: '.uno:Undo', '.uno:GoToCell' (args {Nr:'B7'}), '.uno:InsertPagebreak', '.uno:Deselect', '.uno:RecalcPivotTable', '.uno:SelectAll', '.uno:FreezePanes', '.uno:SpellDialog'. It drives the GUI, so it acts on the CURRENT selection/view — set that up first (e.g. calc_select_range).",
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
     "description": "Insert an auto-numbering caption, e.g. 'Figure 1 — Site plan'. 'category' names the number sequence (Figure/Table/...; numbers increment across captions sharing a category, and LibreOffice renumbers them automatically). Anchor it to a TABLE or an IMAGE by name — the usual case, and the caption then sits above the table / below the figure by convention — or to a text 'search' match, or append at the end. Use writer_list_tables / writer_list_figures to get the names.",
     "inputSchema": _schema({"category": dict(_STR, description="sequence name, e.g. 'Figure' or 'Table'"),
                             "text": dict(_STR, description="caption label"),
                             "table": dict(_STR, description="caption this table (name from writer_list_tables)"),
                             "image": dict(_STR, description="caption this image (name from writer_list_figures)"),
                             "position": dict(_STR, enum=["before", "after"], description="relative to the table/image; defaults to before for tables, after for images"),
                             "separator": dict(_STR, description="between number and label (default ' — ')"),
                             "numbering": dict(_STR, enum=["arabic", "roman_upper", "roman_lower", "letter_upper", "letter_lower"]),
                             "search": dict(_STR, description="place caption after this text's paragraph"),
                             "match_case": _BOOL})},
    # --- failure recovery ---
    {"name": "lo_health",
     "description": "Pre-flight check before a risky edit: connection and transport, the call timeout, every open document with whether it has UNSAVED changes and a real file, stale .~lock files left by a crash, pending crash-recovery, and whether AutoSave is on. Returns a 'problems' list and a 'healthy' flag. Call this when something has gone wrong, or before a large/destructive change.",
     "inputSchema": _schema()},
    {"name": "lo_recover",
     "description": "LibreOffice's crash recovery, driven over UNO instead of the startup dialog. action 'status' (default) reports whether it crashed, what is waiting to be recovered and the AutoSave setting; 'restore' reopens the pending documents; 'discard' permanently destroys that unsaved work and needs confirm=true; 'set_autosave' turns AutoSave on with 'minutes' (0 = off).",
     "inputSchema": _schema({"action": dict(_STR, enum=["status", "restore", "discard", "set_autosave"]),
                             "minutes": dict(_INT, description="set_autosave: interval in minutes, 0 to disable"),
                             "confirm": dict(_BOOL, description="discard: required, destroys unsaved work from the crash")})},
    {"name": "document_watch",
     "description": "Notice when a document changes underneath you — in particular when the USER edits it while Claude is thinking. action 'start' begins watching, 'check' reports how many changes happened and separates OUR edits from the user's, 'stop' ends it, 'list' shows what is watched. Start a watch before a long or multi-step operation, then check before overwriting anything.",
     "inputSchema": _schema({"action": dict(_STR, enum=["start", "check", "stop", "list"]),
                             "title": dict(_STR, description="match the document by window-title substring"),
                             "url": dict(_STR, description="match the document by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
    {"name": "checkpoint_document",
     "description": "Snapshot a document to a side file so a risky edit can be undone. THIS IS THE ONLY ROLLBACK for anything that writes a cell range — LibreOffice does not record bulk range writes for undo, so Ctrl+Z cannot bring those back. action 'create' (default) saves a copy and returns a checkpoint_id, 'list' shows saved checkpoints, 'restore' puts one back (closing and reopening the document; unsaved edits since the checkpoint are lost).",
     "inputSchema": _schema({"action": dict(_STR, enum=["create", "list", "restore"]),
                             "checkpoint_id": dict(_STR, description="restore: the id from create/list"),
                             "title": dict(_STR, description="match the document by window-title substring"),
                             "url": dict(_STR, description="match the document by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
    {"name": "writer_captions",
     "description": "List or re-word existing captions. action 'list' returns every auto-numbered caption (index, category, number, label) — including ones made with LibreOffice's own Insert > Caption. action 'set' rewrites the LABEL of the caption picked by 'index', 'search' or 'category', leaving the number a live field so renumbering still works. To delete a caption outright use writer_delete_paragraphs.",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "set"]),
                             "text": dict(_STR, description="set: the new caption label"),
                             "index": dict(_INT, description="set: 0-based index from action='list'"),
                             "search": dict(_STR, description="set: match captions containing this text"),
                             "category": dict(_STR, description="set: match captions in this sequence, e.g. 'Figure'"),
                             "separator": dict(_STR, description="between number and label (default ' — ')")})},
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
                             "regex": dict(_BOOL, description="treat 'search' as a Python regular expression"),
                             "style": dict(_STR, description="only paragraphs in this paragraph style, e.g. 'Heading 1' — give it WITHOUT 'search' to list every heading"),
                             "limit": dict(_INT, description="max matching paragraphs (default 100)")})},
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
    # --- upstream-parity: document ops, macros, dispatcher, calc convenience ---
    {"name": "convert",
     "description": "Headlessly convert document(s) to another format via LibreOffice filters (docx/xlsx->pdf, odt->docx, ...). Give 'path' (one) or 'paths' (many) + target 'to'; outputs land beside each source or in 'output_dir'. Each file is loaded hidden, stored, and closed — the active document is untouched.",
     "inputSchema": _schema({"path": dict(_STR, description="a single source file"),
                             "paths": {"type": "array", "items": {"type": "string"},
                                       "description": "multiple source files"},
                             "to": dict(_STR, description="target format: pdf/docx/odt/xlsx/ods/csv/txt"),
                             "output_dir": dict(_STR, description="output directory (default: beside each source)")})},
    {"name": "merge",
     "description": "Merge several Writer/text documents into one, in order, with a page break between them; save to 'output'. Text documents only (Calc/PDF merge out of scope).",
     "inputSchema": _schema({"paths": {"type": "array", "items": {"type": "string"},
                                       "description": "ordered source files (>= 2)"},
                             "output": dict(_STR, description="merged output path")},
                            ["paths", "output"])},
    {"name": "list_templates",
     "description": "List document templates under LibreOffice's configured Template paths: [{name, path}] plus the directories scanned.",
     "inputSchema": _schema()},
    {"name": "create_from_template",
     "description": "Create a new untitled document from a template file (.ott/.ots/...).",
     "inputSchema": _schema({"path": _STR}, ["path"])},
    {"name": "run_python_macro",
     "description": "Invoke a PYTHON macro via the script provider (complements run_macro's Basic). 'name' is a full vnd.sun.star.script: URI, or 'file.py$function' resolved at 'location' (user/share/document; default user). Returns the macro's return value.",
     "inputSchema": _schema({"name": _STR,
                             "location": dict(_STR, description="user|share|document (default user)"),
                             "args": {"type": "array", "items": {}, "description": "positional arguments"}},
                            ["name"])},
    {"name": "list_macros",
     "description": "Discover macros: document Basic libraries -> modules, plus user Python script files. Best-effort (application Basic isn't always enumerable).",
     "inputSchema": _schema()},
    {"name": "dispatch",
     "description": "Escape hatch to EVERY tool this server has, including the ones not advertised in the current tier: run any of them by name — {tool, args}. Omit 'tool' (or use 'list'/'help') for the full catalog of names + one-line usage. Use this whenever the advertised set has no tool for the job — the catalog is the authoritative list of what is possible.",
     "inputSchema": _schema({"tool": dict(_STR, description="tool name to run; omit or 'list' for the catalog"),
                             "args": {"type": "object", "description": "arguments for that tool"}})},
    {"name": "calc_statistics",
     "description": "Descriptive statistics over the NUMERIC cells in a Calc range: count, sum, mean, min, max, median, and population stdev. Text/empty cells ignored.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET}, ["range"])},
    {"name": "read_spreadsheet",
     "description": "Read every sheet's used range at once: {sheet_name: 2-D values} — a whole workbook in one call instead of one calc_read_range per sheet.",
     "inputSchema": _schema()},
    # --- everyday composites ---
    {"name": "calc_overview",
     "description": "Map the workbook cheaply before reading it: per sheet the used range, its row/column count, a few sample rows and whether row 1 looks like headers. Output stays small on a huge file — prefer this over read_spreadsheet to get your bearings.",
     "inputSchema": _schema({"sample_rows": dict(_INT, description="sample rows per sheet, 0-20 (default 3)")})},
    {"name": "calc_format_table",
     "description": "Make a data range look like a finished table in ONE call: bold coloured header, full border grid, auto-fitted columns and a frozen header row. Presets: clean (grey header), report (blue header), financial (blue header + #,##0.00 on the body). Defaults to the sheet's used range.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "preset": dict(_STR, enum=["clean", "report", "financial"]),
                             "header": dict(_BOOL, description="treat row 1 as a header (default true)"),
                             "freeze": dict(_BOOL, description="freeze the header row (default true)")})},
    {"name": "calc_clean_data",
     "description": "Tidy a pasted or imported range: trim stray whitespace, turn numeric-looking text into real numbers, and drop fully empty rows. Formula cells are never rewritten. Defaults to the sheet's used range. NOTE: LibreOffice does not record bulk range writes for undo, so Ctrl+Z restores the deleted rows but not the trimmed values — say what will change before running it on data the user cannot re-import.",
     "inputSchema": _schema({"range": _RANGE, "sheet": _SHEET,
                             "drop_empty_rows": dict(_BOOL, description="default true")})},
    {"name": "writer_format_document",
     "description": "Make a Writer document presentable in ONE call: base font and size (all scripts, so Arabic/CTL takes effect), line spacing and page margins. Presets: report (sans 11pt, 20mm, 1.15), essay (serif 12pt, 1in, double), letter (serif 12pt, 25mm, single).",
     "inputSchema": _schema({"preset": dict(_STR, enum=["report", "essay", "letter"]),
                             "font_name": dict(_STR, description="override the preset font"),
                             "font_size": dict(_NUM, description="override the preset size (pt)")})},
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
    {"name": "calc_detect_errors",
     "description": "Find every broken formula in the workbook — #REF!, #DIV/0!, #NAME?, #VALUE!, circular references — reporting the sheet, cell, what the error means and the formula that caused it. Scans all sheets unless 'sheet' is given. Use this when a spreadsheet 'stopped working' or shows error markers.",
     "inputSchema": _schema({"sheet": _SHEET,
                             "max_results": dict(_INT, description="cap the list (default 200)")})},
    {"name": "list_recent_documents",
     "description": "List the documents from LibreOffice's File > Recent Documents, newest first, with title and file path — so a user who says 'open the essay I was working on' can be offered the right file without knowing where it lives.",
     "inputSchema": _schema({"limit": dict(_INT, description="how many to return (default 15)")})},
    {"name": "print_document",
     "description": "Send a document to a PHYSICAL printer — this consumes real paper. Only call it when the user has actually asked to print, and confirm the printer and page range first if there is any doubt. Targets a specific open doc by index/title/url, else the active one.",
     "inputSchema": _schema({"printer": dict(_STR, description="printer name (default: the system default printer)"),
                             "pages": dict(_STR, description="page range like '1-4' or '1,3,5' (default: all pages)"),
                             "copies": dict(_INT, description="number of copies (default 1)"),
                             "title": dict(_STR, description="match the document by window-title substring"),
                             "url": dict(_STR, description="match the document by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
    {"name": "writer_resolve_comment",
     "description": "Mark Writer comment(s) resolved or unresolved — the write side of what writer_get_comments reports. Pick by 'index' (as listed by writer_get_comments), or by 'search' (comment-text substring) / 'author' to resolve every match. Needs LibreOffice 7.1+.",
     "inputSchema": _schema({"index": dict(_INT, description="0-based index as returned by writer_get_comments"),
                             "search": dict(_STR, description="resolve every comment whose text contains this"),
                             "author": dict(_STR, description="resolve every comment by this author"),
                             "resolved": dict(_BOOL, description="true = resolved (default), false = reopen")})},
    # --- impress (presentations) — slides addressed by 1-based index ---
    {"name": "impress_overview",
     "description": "Read the presentation: slide count and, per slide, its 1-based index, layout, title, body text length, and whether it has speaker notes. The 'orient yourself' tool for a deck — call it first.",
     "inputSchema": _schema()},
    {"name": "impress_add_slide",
     "description": "Add a slide and apply a layout. 'after' (1-based) inserts the new slide right after that slide; omit to append at the end. 'layout' picks the placeholders: title_subtitle, title_content, two_content, title_only, or blank. Returns the new slide's 1-based number.",
     "inputSchema": _schema({"after": dict(_INT, description="insert after this 1-based slide; omit to append"),
                             "layout": dict(_STR, enum=sorted(_IMPRESS_LAYOUTS),
                                            description="slide layout (default title_content)")})},
    {"name": "impress_read_slide",
     "description": "Read one slide in full: its layout, title, body bullets (each with its indent level), the names of any other shapes, and speaker notes. Address it by 1-based 'slide'.",
     "inputSchema": _schema({"slide": dict(_INT, description="1-based slide number")}, ["slide"])},
    {"name": "impress_set_title",
     "description": "Set the title placeholder of slide 'slide' (1-based) to 'text'. The slide needs a layout that has a title (all but 'blank').",
     "inputSchema": _schema({"slide": _INT, "text": _STR}, ["slide", "text"])},
    {"name": "impress_set_content",
     "description": "Fill the content/outline placeholder of slide 'slide' (1-based) with bullet points. 'bullets' is a list of strings, or {'text','level'} objects where level 0 is a top bullet and 1+ indents it. Needs a content layout (e.g. title_content).",
     "inputSchema": _schema({"slide": _INT,
                             "bullets": {"type": "array",
                                         "items": {"type": ["string", "object"]},
                                         "description": "strings or {text, level} objects"}},
                            ["slide", "bullets"])},
    {"name": "impress_set_notes",
     "description": "Set the speaker notes of slide 'slide' (1-based) to 'text'. Notes are what the presenter sees, not the audience.",
     "inputSchema": _schema({"slide": _INT, "text": _STR}, ["slide", "text"])},
    {"name": "impress_insert_image",
     "description": "Insert an image from a local file 'path' onto slide 'slide' (1-based). Position/size in millimetres (x_mm/y_mm/width_mm/height_mm); size defaults to the image's own dimensions.",
     "inputSchema": _schema({"slide": _INT,
                             "path": dict(_STR, description="local image file"),
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["slide", "path"])},
    {"name": "impress_insert_shape",
     "description": "Add an auto shape (rectangle, ellipse, line, text) to slide 'slide' (1-based) with optional 'text' and 'fill_color' (hex like '#4472C4'). Position/size in millimetres.",
     "inputSchema": _schema({"slide": _INT,
                             "kind": dict(_STR, enum=sorted(_DRAW_SHAPES),
                                          description="shape kind (default rectangle)"),
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM,
                             "text": _STR, "fill_color": _STR},
                            ["slide"])},
    {"name": "impress_insert_text_box",
     "description": "Add a free-floating text box to slide 'slide' (1-based) holding 'text', positioned/sized in millimetres. For text outside the layout placeholders.",
     "inputSchema": _schema({"slide": _INT, "text": _STR,
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["slide", "text"])},
    {"name": "impress_set_layout",
     "description": "Change the autolayout of slide 'slide' (1-based) to 'layout' (title_subtitle, title_content, two_content, title_only, blank). Reflows the placeholders; existing placeholder text is kept where a matching box remains.",
     "inputSchema": _schema({"slide": _INT,
                             "layout": dict(_STR, enum=sorted(_IMPRESS_LAYOUTS))},
                            ["slide", "layout"])},
    {"name": "impress_delete_slide",
     "description": "Delete slide 'slide' (1-based). Refuses to delete the last remaining slide.",
     "inputSchema": _schema({"slide": _INT}, ["slide"])},
    {"name": "impress_duplicate_slide",
     "description": "Duplicate slide 'slide' (1-based); the copy is inserted immediately after it. Returns the new slide's 1-based number.",
     "inputSchema": _schema({"slide": _INT}, ["slide"])},
    {"name": "impress_set_transition",
     "description": "Set the slide-change transition on slide 'slide' (1-based) or every slide ('all':true). 'type': none, fade, wipe, push, cover, uncover, dissolve, wheel, cut. 'duration' is the effect length (seconds); 'advance_secs' auto-advances after N seconds (omit = advance on click).",
     "inputSchema": _schema({"slide": _INT, "all": _BOOL,
                             "type": dict(_STR, enum=sorted(_IMPRESS_TRANSITIONS)),
                             "duration": _NUM,
                             "advance_secs": dict(_NUM, description="auto-advance after N seconds; omit for on-click")})},
    {"name": "impress_export_slides",
     "description": "Render slides to image files in directory 'dir' — one file per slide (slide-01.png, ...). 'format': png, svg, or jpg. Exports all slides unless 'slide' (1-based) is given. This is real rendering, not available to .pptx file writers.",
     "inputSchema": _schema({"dir": dict(_STR, description="output directory (created if missing)"),
                             "format": dict(_STR, enum=sorted(_SLIDE_IMG_FILTERS)),
                             "slide": dict(_INT, description="export only this 1-based slide"),
                             "all": _BOOL},
                            ["dir"])},
    {"name": "impress_insert_table",
     "description": "Insert a table on slide 'slide' (1-based). Give 'rows'+'cols', or a 'data' grid (list of rows) to size and fill it in one call. Position/size in millimetres.",
     "inputSchema": _schema({"slide": _INT,
                             "rows": _INT, "cols": _INT,
                             "data": {"type": "array", "items": {"type": "array"},
                                      "description": "rows of cell values"},
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["slide"])},
    {"name": "impress_insert_chart",
     "description": "Insert a data chart on slide 'slide' (1-based). 'chart_type': column, bar, line, area, pie. 'data' is a grid whose first row is the series headers and first column is the category labels (e.g. [['','2023','2024'],['APAC',10,14],['EMEA',8,9]]). Optional 'title'. Position/size in millimetres.",
     "inputSchema": _schema({"slide": _INT,
                             "chart_type": dict(_STR, enum=sorted(_CHART_DIAGRAMS)),
                             "data": {"type": "array", "items": {"type": "array"},
                                      "description": "grid: row 0 = series headers, col 0 = category labels"},
                             "title": _STR,
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["slide", "data"])},
    {"name": "impress_slideshow",
     "description": "Control the on-screen slideshow: action 'start' (optionally 'from_slide', 1-based), 'stop', or 'status'. Starting launches the show in the LibreOffice window, so it needs a GUI session (not a headless office). Returns whether a show is running.",
     "inputSchema": _schema({"action": dict(_STR, enum=["start", "stop", "status"]),
                             "from_slide": dict(_INT, description="1-based slide to start from")})},
    {"name": "impress_set_background",
     "description": "Set a slide background on slide 'slide' (1-based) or every slide ('all':true): a solid 'color' (hex like '#2E4053'), an 'image' (local file, stretched to fill), or both, with optional 'transparency' (0 opaque..100 invisible — e.g. 70 for a faint watermark). Renders behind the content; calling again replaces it.",
     "inputSchema": _schema({"slide": _INT, "all": _BOOL,
                             "color": dict(_STR, description="hex colour, e.g. '#2E4053'"),
                             "image": dict(_STR, description="local image file to stretch across the slide"),
                             "transparency": dict(_INT, description="0 (opaque) to 100 (invisible)")})},
    # --- draw (vector drawings) — pages addressed by 1-based index ---
    {"name": "draw_overview",
     "description": "Read a Draw document: page count and, per page, its 1-based index, name, and shape count. The 'orient yourself' tool for a drawing.",
     "inputSchema": _schema()},
    {"name": "draw_read_page",
     "description": "List the shapes on Draw page 'page' (1-based): each shape's index, name, kind, and any text.",
     "inputSchema": _schema({"page": _INT}, ["page"])},
    {"name": "draw_add_page",
     "description": "Append a new page to the Draw document, optionally naming it. Returns the new page's 1-based number.",
     "inputSchema": _schema({"name": _STR})},
    {"name": "draw_insert_shape",
     "description": "Add an auto shape (rectangle, ellipse, line, text) to Draw page 'page' (1-based, default 1) with optional 'text' and 'fill_color' (hex). Position/size in millimetres.",
     "inputSchema": _schema({"page": _INT,
                             "kind": dict(_STR, enum=sorted(_DRAW_SHAPES)),
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM,
                             "text": _STR, "fill_color": _STR})},
    {"name": "draw_insert_text_box",
     "description": "Add a text box holding 'text' to Draw page 'page' (1-based, default 1). Position/size in millimetres.",
     "inputSchema": _schema({"page": _INT, "text": _STR,
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["text"])},
    {"name": "draw_insert_image",
     "description": "Insert an image from local file 'path' onto Draw page 'page' (1-based, default 1). Position/size in millimetres; size defaults to the image's own dimensions.",
     "inputSchema": _schema({"page": _INT,
                             "path": dict(_STR, description="local image file"),
                             "x_mm": _NUM, "y_mm": _NUM,
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["path"])},
    {"name": "draw_insert_connector",
     "description": "Draw a connector line on Draw page 'page' (1-based, default 1) from (x1_mm,y1_mm) to (x2_mm,y2_mm). Optionally glue its ends to shapes by 1-based shape index (start_shape/end_shape) so the connector follows them. Draw's diagramming primitive.",
     "inputSchema": _schema({"page": _INT,
                             "x1_mm": _NUM, "y1_mm": _NUM,
                             "x2_mm": _NUM, "y2_mm": _NUM,
                             "start_shape": _INT, "end_shape": _INT})},
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


# The everyday surface. The full 174 are ~76 KB of JSON schema — roughly 22k
# tokens injected into EVERY conversation, and 68 calc_* lookalikes to choose
# between for "make this table look nice". These are the ones a student or an
# everyday user actually reaches for; `dispatch` still reaches all the rest by
# name, so nothing is lost — set LO_TOOLS=full to advertise them all flat.
_BASIC_TOOLS = frozenset("""
lo_status list_documents get_current_selection document_undo dispatch
create_document open_document save_document close_document export_document convert
calc_overview calc_read_range calc_write_range calc_set_formulas calc_list_sheets
calc_get_used_range calc_format_table calc_clean_data calc_format_range
calc_sort_range calc_create_chart calc_add_sheet
calc_import_csv calc_detect_errors
writer_get_text writer_append_text writer_replace_selection writer_find_replace
writer_format_document writer_insert_heading writer_insert_table
writer_apply_style writer_format_text
writer_get_comments writer_resolve_comment
writer_insert_image writer_insert_caption writer_captions
list_recent_documents print_document
lo_health lo_recover checkpoint_document document_watch
print_settings set_alt_text writer_content_control document_lifecycle
set_document_properties insert_form_control export_document
impress_overview impress_read_slide impress_add_slide
impress_set_title impress_set_content impress_set_notes
impress_insert_image impress_insert_shape
impress_set_transition impress_export_slides impress_insert_table
impress_insert_chart impress_set_background
draw_overview draw_read_page draw_insert_shape draw_insert_text_box
draw_insert_image draw_insert_connector
""".split())


def _full_tier():
    """True when the full flat surface is requested. Accepts the manifest's
    boolean checkbox ('true'/'1') as well as an explicit LO_TOOLS=full."""
    return (os.environ.get("LO_TOOLS", "").strip().lower()
            in ("full", "all", "true", "yes", "1"))


def _advertised_tools():
    """tools/list payload for the configured tier. Anything unrecognised falls
    back to basic rather than erroring — a typo in a GUI config field must not
    leave the user with no tools at all."""
    if _full_tier():
        return TOOL_DEFS
    return [d for d in TOOL_DEFS if d["name"] in _BASIC_TOOLS]


def _bundled_oxt():
    """Path to the agent-acceptor .oxt shipped inside the bundle, if present.
    Installing it is what lets Claude reach a LibreOffice the user opened
    normally — no port, no relaunch, no command-line flags."""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.path.join(here, "..", "ext"), here):
        try:
            for fn in sorted(os.listdir(base)):
                if fn.endswith(".oxt"):
                    return os.path.abspath(os.path.join(base, fn))
        except OSError:
            continue
    return None


# Sent once, in the initialize reply. This is where a server says HOW it wants
# to be used — the standard place for it, and cheaper than repeating guidance in
# 186 tool descriptions. Spend it on decisions, not on trivia.
SERVER_INSTRUCTIONS = """\
You are editing documents that are open in LibreOffice on the user's own screen.
Edits appear immediately and there is no separate commit step, so treat the
document as the user's live work, not a draft you own.

START by calling document_lifecycle. It reads the document and tells you which
phase it is in, what is already done, and the next few concrete steps. Call it
again whenever you are unsure what to do next, or after finishing a stage.

The three phases are advisory, and EVERY tool works in every phase — if the user
asks to export while you are still writing, just export.

  SETUP     Agree what the document is and who it is for. Set the title and the
            language, and settle the look (writer_format_document, or
            calc_format_table) BEFORE writing content — restyling later is far
            more disruptive than getting it right first.

  AUTHORING Build the content in visible increments. Use real headings rather
            than bold text, because headings drive the navigation, the table of
            contents and the PDF outline. Show the user what changed and ask
            before moving to the next section.

  CLOSING   Record the metadata (author, subject, keywords, rights), give every
            image alt text, save, and ask what kind of export they want — a
            plain PDF, an accessible one (tagged/pdfua), or a fillable form
            (form_fields).

BE INTERACTIVE. Ask before big or destructive changes, and show intermediate
results rather than doing everything in one silent burst. When the user's intent
is ambiguous, ask a single specific question instead of guessing.

BEFORE ANY LARGE OR DESTRUCTIVE EDIT, call checkpoint_document. LibreOffice does
NOT record bulk cell-range writes for undo, so Ctrl+Z will not bring back data
that calc_write_range overwrote — a checkpoint is the only way back.

Only about a quarter of the tools are advertised by default. `dispatch` with
tool='list' is the authoritative catalog of everything this server can do; check
it before telling a user something is not possible.

If a call fails, read the structured error: `retryable` says whether to try
again, and `hint` says what to do instead. lo_health explains most "it stopped
working" situations."""


# Server-side prompts: the closest thing MCP has to a shipped skill. Claude
# Desktop surfaces these for the USER to pick, which is what makes the session
# interactive rather than the model guessing when to start a workflow.
PROMPTS = [
    {"name": "start_document",
     "description": "Begin a new document with Claude: agree the purpose, then "
                    "set the title, language and house style before writing.",
     "arguments": [
         {"name": "kind", "description": "'writer' or 'calc'", "required": False},
         {"name": "about", "description": "what the document is for", "required": False}],
     "text": "I want to start a new {kind} document about {about}.\n\n"
             "Call document_lifecycle first. Then, before writing any content,\n"
             "walk me through the setup phase one question at a time:\n"
             "  1. what this document is for and who will read it\n"
             "  2. a title, and the language it should be in\n"
             "  3. the look — page size, margins, base font, heading style\n"
             "Apply each choice as we agree it so I can see it, and do not start\n"
             "writing the body until I say the setup looks right."},
    {"name": "review_document",
     "description": "Check the open document for problems before it goes out: "
                    "broken formulas, missing alt text, incomplete metadata.",
     "arguments": [],
     "text": "Review the document that is open.\n\n"
             "Call document_lifecycle and lo_health, and for a spreadsheet also\n"
             "calc_detect_errors. Then tell me, as a short list:\n"
             "  - anything actually broken\n"
             "  - anything missing before this could be shared\n"
             "  - what you suggest doing about each\n"
             "Do not change anything yet — show me the list and let me choose."},
    {"name": "finish_document",
     "description": "Close out a document: metadata, accessibility, save, and "
                    "the right kind of export.",
     "arguments": [
         {"name": "purpose", "description": "e.g. 'email it', 'print it', 'a fillable form'",
          "required": False}],
     "text": "I am finished writing. Help me close this document out for: {purpose}\n\n"
             "Call document_lifecycle for the closing checklist, then take me\n"
             "through it one step at a time:\n"
             "  1. metadata — author, subject, keywords, and any licence\n"
             "  2. alt text for any image that lacks it\n"
             "  3. where to save it\n"
             "  4. the export — ask whether I need a plain PDF, an accessible\n"
             "     one (tagged/PDF-UA), or a fillable form, and explain the\n"
             "     difference briefly rather than choosing for me.\n"
             "Confirm each step with me before doing the next."},
]


def handle(message):
    method = message.get("method")
    mid = message.get("id")

    if method == "initialize":
        params = message.get("params") or {}
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return _result(mid, {
            "protocolVersion": version,
            "capabilities": {"tools": {}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })
    if method == "prompts/list":
        return _result(mid, {"prompts": [
            {"name": p["name"], "description": p["description"],
             "arguments": p.get("arguments", [])} for p in PROMPTS]})
    if method == "prompts/get":
        params = message.get("params") or {}
        wanted = params.get("name")
        prompt = next((p for p in PROMPTS if p["name"] == wanted), None)
        if prompt is None:
            return _error(mid, -32602, "Unknown prompt: %s" % wanted)
        body = prompt["text"]
        for key, value in (params.get("arguments") or {}).items():
            body = body.replace("{%s}" % key, str(value))
        return _result(mid, {
            "description": prompt["description"],
            "messages": [{"role": "user",
                          "content": {"type": "text", "text": body}}]})
    if method == "notifications/initialized":
        return None  # notification, no reply
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": _advertised_tools()})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        func = TOOLS.get(name)
        if func is None:
            return _error(mid, -32602, "Unknown tool: %s" % name)
        try:
            payload = _run_with_timeout(
                lambda: _call_with_reconnect(func, args, name), _call_timeout())
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
            # Two blocks, mirroring the success shape: a human-readable line
            # first, then the structured form the caller can branch on.
            # UNO exceptions often have an EMPTY str() — always name the type.
            info = _classify_error(exc)
            line = "Error [%s] %s: %s" % (info["code"], info["error_type"],
                                          info["message"])
            if info["hint"]:
                line += "\nHint: " + info["hint"]
            return _result(mid, {"content": [
                {"type": "text", "text": line},
                {"type": "text", "text": json.dumps(info, ensure_ascii=False)}],
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
    _log("LibreOffice MCP server ready (stdio, %d of %d tools advertised; "
         "LO_TOOLS=%s). LO_UNO_PORT=%s"
         % (len(_advertised_tools()), len(TOOLS),
            os.environ.get("LO_TOOLS", "basic"),
            os.environ.get("LO_UNO_PORT", "2002")))
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
