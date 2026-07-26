# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Shared machinery: the UNO bridge, document selection, undo grouping,
error classification, call timeouts and the small UNO helpers.

Everything here is used by more than one tool family. Tool modules import it
wholesale; nothing here knows what a tool is.
"""

import json
import os
import sys

from .registry import (TOOLS, TOOL_DEFS, BASIC_TOOLS as _BASIC_TOOLS,
                       NO_UNDO as _NO_UNDO, advertised)

SERVER_NAME = "libreoffice"
SERVER_VERSION = "0.9.6"
DEFAULT_PROTOCOL = "2024-11-05"

# ../../src from mcp/loconn/core.py — the shared uno_bridge the .oxt also uses
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))), "src")


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


# --------------------------------------------------------------------------- #
# Tools — Writer P1 (see docs/TOOLS-WANTED.md)
# --------------------------------------------------------------------------- #

def _enum_value(v):
    """pyuno Enum -> its string value (e.g. 'AT_PARAGRAPH'); str() otherwise."""
    return getattr(v, "value", None) or str(v)


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


# --------------------------------------------------------------------------- #
# Tools — Calc P1/P2/P3 — see docs/TOOLS-WANTED.md
# --------------------------------------------------------------------------- #

_CALC_SHAPES = dict(_DRAW_SHAPES)


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


# --------------------------------------------------------------------------- #
# Tools — failure recovery
#
# The auto-launch keeps --norestore ON PURPOSE: without it a pending crash makes
# LibreOffice open its recovery dialog at startup, and that dialog blocks the
# UNO socket from ever opening — auto-launch would hang instead of connecting.
# So recovery is not silently discarded, it is DETECTED here (lo_health,
# lo_recover) and restored deliberately over UNO, with no dialog involved.
# --------------------------------------------------------------------------- #

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


def _note_our_edit():
    """Called after every successful mutating tool, so a watcher can subtract
    the edits WE made from the edits the user made."""
    if not _WATCHERS:
        return
    with _watch_lock():
        for entry in _WATCHERS.values():
            entry["ours"] += 1


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


# --------------------------------------------------------------------------- #
# Tier selection
#
# Lives here rather than in the entry point because lo_status and lo_health
# report the counts, and they are ordinary tools.
# --------------------------------------------------------------------------- #

def _full_tier():
    """True when the full flat surface is requested. Accepts the manifest's
    boolean checkbox ('true'/'1') as well as an explicit LO_TOOLS=full."""
    return (os.environ.get("LO_TOOLS", "").strip().lower()
            in ("full", "all", "true", "yes", "1"))


def _advertised_tools():
    """tools/list payload for the configured tier. Anything unrecognised falls
    back to basic rather than erroring — a typo in a GUI config field must not
    leave the user with no tools at all."""
    return advertised(_full_tier())


# `from .core import *` skips names beginning with an underscore, and nearly
# every helper here is _private by convention — so export them explicitly.
# Dunders stay out; everything else a tool module might need is in.
__all__ = [_n for _n in dir() if not _n.startswith("__")]
