# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Writer tools — structure."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




def tool_writer_insert_heading(args):
    doc = _require_writer()
    level = int(args.get("level", 1))
    if not 1 <= level <= 6:
        raise RuntimeError("level must be 1..6")
    text, cursor = _append_paragraph(doc, style="Heading %d" % level)
    text.insertString(cursor, args["text"], False)
    return {"heading": args["text"], "level": level}


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


TOOL_DEFS = [
    {"name": "writer_insert_heading",
     "description": "Append a heading paragraph (styles 'Heading 1'..'Heading 6') at the end of the document.",
     "inputSchema": _schema({"text": _STR, "level": dict(_INT, minimum=1, maximum=6)}, ["text"])},
    {"name": "writer_get_outline",
     "description": "List the document's headings/subheadings as an outline: [{level, text, index, style}, ...]. 'level' is the outline depth (1 = heading, 2 = subheading, 3 = sub-subheading, ...); 'index' is the body-paragraph index for targeting with writer_format_paragraph / writer_apply_style / writer_move_paragraphs.",
     "inputSchema": _schema()},
    {"name": "writer_add_conditional_section",
     "description": "Writer's analog of conditional formatting: append text wrapped in a named CONDITIONAL SECTION that is HIDDEN when 'condition' evaluates true (LibreOffice field syntax, e.g. '1==1', 'user_field==\"x\"'). The condition is evaluated by Writer's layout when the document is viewed/printed. Set visible=false to hide the section immediately regardless of condition.",
     "inputSchema": _schema({"name": dict(_STR, description="unique section name"),
                             "condition": dict(_STR, description="hide-when-true condition, e.g. '1==1'"),
                             "text": _STR, "visible": _BOOL},
                            ["name", "condition"])},
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
    {"name": "writer_mail_merge",
     "description": "Run a mail merge over Database fields already in the (saved) document, from a registered 'data_source' + 'command' (table/query name), emitting file/printer/mail output. Requires a registered data source.",
     "inputSchema": _schema({"data_source": dict(_STR, description="registered data source name"),
                             "command": dict(_STR, description="table or query name"),
                             "command_type": dict(_STR, enum=["table", "query", "command"]),
                             "output": dict(_STR, enum=["file", "printer", "mail"]),
                             "output_url": dict(_STR, description="output folder path (file output)")},
                            ["data_source", "command"])},
    {"name": "writer_set_chapter_numbering",
     "description": "Turn on heading (chapter) numbering: bind the first 'levels' outline levels (default 3) to a scheme so Heading 1/2/3 auto-number as 1, 1.1, 1.1.1. numbering arabic/roman_upper/roman_lower/letter_upper/letter_lower/none; 'separator' between/after numbers (default '.').",
     "inputSchema": _schema({"levels": dict(_INT, description="how many outline levels to number (default 3)"),
                             "numbering": dict(_STR, enum=["arabic", "roman_upper", "roman_lower", "letter_upper", "letter_lower", "none"]),
                             "separator": dict(_STR, description="separator/suffix, default '.'")})},
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
    {"name": "writer_captions",
     "description": "List or re-word existing captions. action 'list' returns every auto-numbered caption (index, category, number, label) — including ones made with LibreOffice's own Insert > Caption. action 'set' rewrites the LABEL of the caption picked by 'index', 'search' or 'category', leaving the number a live field so renumbering still works. To delete a caption outright use writer_delete_paragraphs.",
     "inputSchema": _schema({"action": dict(_STR, enum=["list", "set"]),
                             "text": dict(_STR, description="set: the new caption label"),
                             "index": dict(_INT, description="set: 0-based index from action='list'"),
                             "search": dict(_STR, description="set: match captions containing this text"),
                             "category": dict(_STR, description="set: match captions in this sequence, e.g. 'Figure'"),
                             "separator": dict(_STR, description="between number and label (default ' — ')")})},
    {"name": "writer_set_line_numbering",
     "description": "Turn document line numbering on ('enable', default true) or off, and set 'interval' (number every Nth line), 'count_empty_lines', and left 'distance_mm' (Tools > Line Numbering).",
     "inputSchema": _schema({"enable": _BOOL,
                             "interval": dict(_INT, description="number every Nth line"),
                             "count_empty_lines": _BOOL,
                             "distance_mm": _NUM})},
    {"name": "writer_list_figures",
     "description": "List images/figures with name, size (mm), anchor type, and the anchoring paragraph's text (often the caption/context) — discovery for writer_replace_image / writer_set_image_layout.",
     "inputSchema": _schema()},
]

register(globals(), TOOL_DEFS,
         basic=['writer_captions', 'writer_content_control', 'writer_insert_caption', 'writer_insert_heading'],
         read_only=['writer_get_outline', 'writer_list_figures'])
