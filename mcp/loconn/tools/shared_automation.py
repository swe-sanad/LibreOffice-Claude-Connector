# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Shared tools — automation."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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


def tool_dispatch_uno(args):
    doc = _current_doc()
    command = args["command"]
    props = tuple(_pv(k, v) for k, v in (args.get("args") or {}).items())
    self_res = _dispatch(doc, command, props)
    return {"dispatched": command, "handled": self_res is not None}


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


TOOL_DEFS = [
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
    {"name": "run_macro",
     "description": "Invoke a macro in the active document and return its result. 'name' is 'Library.Module.Sub' (document Basic), 'Module.Sub' (Standard library), or a full vnd.sun.star.script: URI.",
     "inputSchema": _schema({"name": dict(_STR, description="e.g. 'KahataynForms.Engine.RefreshView'"),
                             "args": {"type": "array", "description": "positional arguments"}},
                            ["name"])},
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
    {"name": "dispatch_uno",
     "description": "Execute an arbitrary .uno: command against the active frame. This is the widest escape hatch there is: EVERY menu item and toolbar button in LibreOffice is a .uno: command, including many with no model-level API at all — so when no dedicated tool fits, this usually still can. Examples: '.uno:Undo', '.uno:GoToCell' (args {Nr:'B7'}), '.uno:InsertPagebreak', '.uno:Deselect', '.uno:RecalcPivotTable', '.uno:SelectAll', '.uno:FreezePanes', '.uno:SpellDialog'. It drives the GUI, so it acts on the CURRENT selection/view — set that up first (e.g. calc_select_range).",
     "inputSchema": _schema({"command": dict(_STR, description="e.g. '.uno:GoToCell'"),
                             "args": {"type": "object", "description": "named PropertyValue args"}},
                            ["command"])},
    {"name": "bind_document_event",
     "description": "Bind (or clear) a Basic/script macro to a document event such as OnSave, OnLoad, OnModifyChanged, OnPrint. Omit 'script' to clear the binding.",
     "inputSchema": _schema({"event": dict(_STR, description="e.g. 'OnSave'"),
                             "script": dict(_STR, description="vnd.sun.star.script: URI (omit to clear)")},
                            ["event"])},
    {"name": "list_embedded_objects",
     "description": "List embedded images and OLE objects with name, type, and size (mm). Writer: graphics + embedded objects. Calc: DrawPage graphic/OLE shapes across all sheets.",
     "inputSchema": _schema()},
    {"name": "insert_ole_object",
     "description": "Embed an OLE object. Give 'object' (math/calc/chart) or a raw 'clsid'. Writer: inserts at the end. Calc: adds to a sheet's DrawPage at the given size.",
     "inputSchema": _schema({"object": dict(_STR, enum=["math", "calc", "chart"]),
                             "clsid": dict(_STR, description="explicit component CLSID"),
                             "sheet": _SHEET, "width_mm": _NUM, "height_mm": _NUM})},
    {"name": "form_control",
     "description": "Manage existing form controls (Writer or Calc). action 'list' returns each control's form/name/type/props; action 'set' updates a control by 'name': label, value, state (0/1/2), enabled, read_only, items (listbox).",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "set"]),
                             "name": dict(_STR, description="control name (set)"),
                             "label": _STR, "value": _STR, "state": _INT,
                             "enabled": _BOOL, "read_only": _BOOL,
                             "items": {"type": "array", "items": _STR}})},
    {"name": "batch",
     "description": "Run several tool calls in one round-trip. 'operations' is a list of {tool, args}; returns each result/error in order. stop_on_error (default true) halts on the first failure. Cuts latency on long multi-step document builds.",
     "inputSchema": _schema({"operations": {"type": "array",
                                            "items": {"type": "object"},
                                            "description": "list of {tool, args}"},
                             "stop_on_error": _BOOL},
                            ["operations"])},
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
]

register(globals(), TOOL_DEFS,
         basic=['dispatch', 'get_current_selection', 'insert_form_control'],
         read_only=['batch', 'dispatch', 'get_current_selection', 'inspect_ods', 'list_embedded_objects', 'list_macros', 'lo_screenshot'])
