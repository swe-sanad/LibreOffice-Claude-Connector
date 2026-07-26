# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Shared tools — properties."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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

    # --- the print switches themselves ---
    # Writer keeps them on the document settings; Calc keeps them on the page
    # style of the active sheet. Same tool, two homes.
    if kind == "calc":
        sheet = doc.getCurrentController().getActiveSheet()
        holder = doc.getStyleFamilies().getByName("PageStyles").getByName(
            sheet.PageStyle)
    else:
        holder = doc.createInstance("com.sun.star.document.Settings")

    options = args.get("options") or {}
    for name, value in options.items():
        if name not in wanted:
            raise RuntimeError(
                "%r is not a print option for a %s document. Available: %s"
                % (name, kind, ", ".join(wanted)))
        holder.setPropertyValue(name, bool(value))
        changed.append(name)

    current = {}
    for name in wanted:
        try:
            current[name] = holder.getPropertyValue(name)
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


TOOL_DEFS = [
    {"name": "get_document_properties",
     "description": "Read the active document's metadata: title/author/subject/keywords/description, created/modified dates + editor, statistics, and custom user-defined properties.",
     "inputSchema": _schema()},
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
    {"name": "set_view_zoom",
     "description": "Set the window zoom: 'percent' (a number) and/or 'type' (optimal/page_width/whole_page/percent/page_width_exact).",
     "inputSchema": _schema({"percent": _INT,
                             "type": dict(_STR, enum=["optimal", "page_width", "whole_page", "percent", "page_width_exact"])})},
    {"name": "get_signatures",
     "description": "Report digital-signature status of the saved document: whether it is signed, validity, signer, and signing date.",
     "inputSchema": _schema()},
]

register(globals(), TOOL_DEFS,
         basic=['document_lifecycle', 'print_settings', 'set_alt_text', 'set_document_properties'],
         read_only=['document_lifecycle', 'get_document_properties', 'get_signatures', 'list_styles', 'print_settings', 'set_view_zoom'])
