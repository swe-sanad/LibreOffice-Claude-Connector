# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Shared tools — lifecycle."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




def tool_list_documents(_args):
    return tool_lo_status(_args)


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


def tool_set_document_modified(args):
    doc = _current_doc()
    if args.get("modified") is not None:
        doc.setModified(bool(args["modified"]))
    return {"modified": bool(doc.isModified())}


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


def tool_set_active_document(args):
    """Focus a specific open document so subsequent reads/writes target it,
    selected by 'title' (substring), 'url' (substring), or 0-based 'index' over
    the open documents. The fix for focus-stealing silently redirecting writes."""
    target = _select_doc(args)
    if target is None:
        raise RuntimeError("Give one of: title, url, or index.")
    _activate(target)
    return {"active": _doc_info(target), "open_count": len(_open_docs())}


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


TOOL_DEFS = [
    {"name": "list_documents",
     "description": "List the documents currently open in LibreOffice.",
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
    # --- automation & inspection ---
    {"name": "reload_document",
     "description": "Store, close and reload the active document from disk. THE verification step after shape/macro work: the in-memory model can lie (e.g. form-control shapes are silently dropped by the ODS writer on RTL sheets) — only a reload shows what actually serialized. Reloads with macros enabled.",
     "inputSchema": _schema({"save": dict(_BOOL, description="store before closing (default true)")})},
    {"name": "set_document_modified",
     "description": "Read the dirty flag and optionally set it: modified=false marks the document saved, true forces it dirty. Returns the resulting state.",
     "inputSchema": _schema({"modified": dict(_BOOL, description="omit to just read; false=clear, true=force")})},
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
    {"name": "document_undo",
     "description": "Undo/redo/clear the active document's undo stack, or just query it (action 'status'). Returns whether undo/redo are possible and the next undo title.",
     "inputSchema": _schema({"action": dict(_STR, enum=["undo", "redo", "clear", "status"])})},
    {"name": "set_active_document",
     "description": "Focus a specific open document so subsequent reads/writes target it — select by 'title' (substring, case-insensitive), 'url' (substring), or 0-based 'index' over the open docs (see list_documents). Fixes focus-stealing that silently redirects writes to the wrong document.",
     "inputSchema": _schema({"title": dict(_STR, description="match by window title substring"),
                             "url": dict(_STR, description="match by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
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
]

register(globals(), TOOL_DEFS,
         basic=['close_document', 'convert', 'create_document', 'document_undo', 'export_document', 'list_documents', 'list_recent_documents', 'open_document', 'print_document', 'save_document'],
         read_only=['close_document', 'convert', 'create_document', 'create_from_template', 'document_undo', 'export_document', 'list_documents', 'list_recent_documents', 'list_templates', 'merge', 'open_document', 'print_document', 'reload_document', 'save_document', 'set_active_document', 'set_document_modified'])
