# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Writer tools — text."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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


def tool_writer_insert_page_break(_args):
    doc = _require_writer()
    _text, cursor = _append_paragraph(doc, style="Standard")
    cursor.BreakType = _uno_enum("com.sun.star.style.BreakType", "PAGE_BEFORE")
    return {"inserted": "page_break"}


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


TOOL_DEFS = [
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
    {"name": "writer_find_replace",
     "description": "Find & replace text across the Writer document. Keeps the formatting of what it replaced: a match spanning several formatting runs (part bold, part not) would otherwise come back chopped along the OLD run boundaries — the replacement now takes the formatting of the match's first character. Set preserve_formatting=false for LibreOffice's raw behaviour. With regex=true, 'search' is an ICU regular expression and $1..$n backreferences work in 'replace'.",
     "inputSchema": _schema({"search": _STR, "replace": _STR,
                             "match_case": _BOOL, "whole_words": _BOOL,
                             "regex": dict(_BOOL, description="treat 'search' as a regular expression"),
                             "preserve_formatting": dict(_BOOL, description="default true")},
                            ["search"])},
    {"name": "writer_insert_page_break",
     "description": "Insert a page break at the end of the Writer document.",
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
    # --- good first tools (single-API wrappers) ---
    {"name": "writer_word_count",
     "description": "Document statistics for the active Writer doc: word, paragraph, character counts and page count.",
     "inputSchema": _schema()},
    {"name": "writer_get_paragraphs",
     "description": "List body paragraphs as [{index, text, style, is_heading}] so callers can target a paragraph by 0-based index or applied style instead of a unique search string. Index counts only body paragraphs (skips tables/frames).",
     "inputSchema": _schema()},
    {"name": "writer_set_paragraph_text",
     "description": "Replace the text of the body paragraph at a 0-based 'index' (the index space writer_get_paragraphs reports). Single paragraph — newlines are not turned into paragraph breaks.",
     "inputSchema": _schema({"index": _INT, "text": _STR}, ["index", "text"])},
    {"name": "writer_delete_paragraphs",
     "description": "Delete body paragraphs by 0-based index: 'count' paragraphs starting at 'start' (default 1), including their paragraph breaks. The index space is the one writer_get_paragraphs reports. Deleting every paragraph leaves one empty paragraph (Writer requires at least one).",
     "inputSchema": _schema({"start": _INT,
                             "count": dict(_INT, description="how many paragraphs to delete (default 1)")},
                            ["start"])},
    {"name": "writer_track_changes",
     "description": "Manage tracked changes: action enable/disable recording, accept_all, reject_all, or list/status (returns recording state + pending redlines with author/type/comment).",
     "inputSchema": _schema({"action": dict(_STR, enum=["enable", "disable", "accept_all", "reject_all", "list", "status"])})},
    {"name": "writer_insert_horizontal_rule",
     "description": "Insert a horizontal divider line at the document end (a paragraph in the 'Horizontal Line' style).",
     "inputSchema": _schema()},
    {"name": "writer_redact",
     "description": "Black out every occurrence of a search term (black text on black background). NOTE: visual redaction only — the underlying text still exists in the file.",
     "inputSchema": _schema({"search": _STR, "match_case": _BOOL}, ["search"])},
    {"name": "writer_spellcheck",
     "description": "Spell-check the document body and return flagged words with suggestions. 'language' is a BCP-47 tag (default 'en-US'); 'max_words' caps results.",
     "inputSchema": _schema({"language": _STR, "max_words": _INT})},
    {"name": "writer_change_case",
     "description": "Change letter case: mode upper/lower/title/sentence. Targets text matching 'search', else a body-paragraph range ('start'/'count', default all). Case only — no effect on Arabic.",
     "inputSchema": _schema({"mode": dict(_STR, enum=["upper", "lower", "title", "sentence"]),
                             "search": dict(_STR, description="change matched text; omit for paragraph range"),
                             "match_case": _BOOL,
                             "start": dict(_INT, description="first paragraph index (0-based)"),
                             "count": dict(_INT, description="how many paragraphs (default: to end)")},
                            ["mode"])},
    {"name": "writer_move_paragraphs",
     "description": "Reorder body paragraphs: move the block of 'count' (default 1) paragraphs starting at 0-based 'start' to index 'to' (the block lands before the paragraph currently there; to == paragraph count appends at the end). Preserves content and formatting. Indices are the writer_get_paragraphs space.",
     "inputSchema": _schema({"start": _INT,
                             "count": dict(_INT, description="how many paragraphs to move (default 1)"),
                             "to": dict(_INT, description="destination index (0-based)")},
                            ["start", "to"])},
    {"name": "writer_find",
     "description": "Locate text WITHOUT changing it: returns each matching body paragraph's 0-based index, occurrence count, a snippet, and its style — so you can then target it by index (writer_set_paragraph_text, writer_format_paragraph, writer_delete_paragraphs, ...). Read-only companion to writer_find_replace.",
     "inputSchema": _schema({"search": _STR, "match_case": _BOOL,
                             "regex": dict(_BOOL, description="treat 'search' as a Python regular expression"),
                             "style": dict(_STR, description="only paragraphs in this paragraph style, e.g. 'Heading 1' — give it WITHOUT 'search' to list every heading"),
                             "limit": dict(_INT, description="max matching paragraphs (default 100)")})},
    {"name": "writer_resolve_comment",
     "description": "Mark Writer comment(s) resolved or unresolved — the write side of what writer_get_comments reports. Pick by 'index' (as listed by writer_get_comments), or by 'search' (comment-text substring) / 'author' to resolve every match. Needs LibreOffice 7.1+.",
     "inputSchema": _schema({"index": dict(_INT, description="0-based index as returned by writer_get_comments"),
                             "search": dict(_STR, description="resolve every comment whose text contains this"),
                             "author": dict(_STR, description="resolve every comment by this author"),
                             "resolved": dict(_BOOL, description="true = resolved (default), false = reopen")})},
]

register(globals(), TOOL_DEFS,
         basic=['writer_append_text', 'writer_find_replace', 'writer_get_comments', 'writer_get_text', 'writer_replace_selection', 'writer_resolve_comment'],
         read_only=['writer_find', 'writer_get_comments', 'writer_get_paragraphs', 'writer_get_text', 'writer_word_count'])
