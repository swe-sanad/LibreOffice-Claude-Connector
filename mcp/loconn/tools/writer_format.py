# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Writer tools — format."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




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


TOOL_DEFS = [
    {"name": "writer_format_text",
     "description": "Apply character formatting (bold/italic/underline/font/size/color) to every match of a search string.",
     "inputSchema": _schema({"search": _STR, "match_case": _BOOL,
                             "bold": _BOOL, "italic": _BOOL, "underline": _BOOL,
                             "font_name": _STR, "font_size": _NUM,
                             "font_color": dict(_STR, description="'#RRGGBB'")}, ["search"])},
    {"name": "writer_insert_image",
     "description": "Insert an image file at the end of the Writer document (size in mm; defaults to the image's own size).",
     "inputSchema": _schema({"path": _STR, "width_mm": _INT, "height_mm": _INT}, ["path"])},
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
    # --- writer P1 ---
    {"name": "writer_list_objects",
     "description": "Enumerate objects in the active Writer doc — graphics, text frames, embedded/OLE objects, and draw shapes (rectangle/ellipse/line/text) — with name, type, anchor, and size (mm). Discovery companion to writer_read_table / writer_get_paragraphs.",
     "inputSchema": _schema()},
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
    # --- writer P2/P3 ---
    {"name": "writer_delete_object",
     "description": "Delete a graphic, text frame, embedded object, draw shape, or text section by name.",
     "inputSchema": _schema({"name": _STR}, ["name"])},
    {"name": "writer_set_image_layout",
     "description": "Set anchor (as_char/char/paragraph/page/frame), text wrap (none/through/parallel/dynamic/left/right), and absolute position (x_mm/y_mm) of an existing image or text frame by name.",
     "inputSchema": _schema({"name": _STR,
                             "anchor": dict(_STR, enum=["as_char", "char", "paragraph", "page", "frame"]),
                             "wrap": dict(_STR, enum=["none", "through", "parallel", "dynamic", "left", "right"]),
                             "x_mm": _NUM, "y_mm": _NUM},
                            ["name"])},
    {"name": "writer_insert_shape",
     "description": "Draw a rectangle/ellipse/line/text shape on the draw page at position/size (mm) with optional fill/line color, caption text, and name.",
     "inputSchema": _schema({"kind": dict(_STR, enum=["rectangle", "ellipse", "line", "text"]),
                             "x_mm": _NUM, "y_mm": _NUM, "width_mm": _NUM, "height_mm": _NUM,
                             "fill_color": _STR, "line_color": _STR, "text": _STR, "name": _STR})},
    {"name": "writer_insert_text_frame",
     "description": "Insert a floating text frame (text box) at the end with a given size (mm), optionally pre-filled with text and named.",
     "inputSchema": _schema({"width_mm": _NUM, "height_mm": _NUM, "text": _STR, "name": _STR})},
    {"name": "writer_set_page_background",
     "description": "Set (color) or clear (clear=true) the page background color on a page style (default 'Standard').",
     "inputSchema": _schema({"color": dict(_STR, description="'#RRGGBB'"),
                             "clear": _BOOL, "page_style": _STR})},
    {"name": "writer_set_watermark",
     "description": "Add a text watermark (empty text clears it) with font, angle, transparency (0-100) and color across all pages.",
     "inputSchema": _schema({"text": _STR, "font": _STR,
                             "angle": _INT, "transparency": _INT, "color": _STR})},
    {"name": "writer_apply_style",
     "description": "Apply a named style to text. kind 'paragraph' (default): target a 'search' match or a start/count paragraph range. kind 'character': requires 'search'. The style must already exist (create it with set_style).",
     "inputSchema": _schema({"style": _STR,
                             "kind": dict(_STR, enum=["paragraph", "character"]),
                             "search": dict(_STR, description="apply to matches; paragraph kind may use start/count instead"),
                             "match_case": _BOOL, "start": _INT, "count": _INT},
                            ["style"])},
    {"name": "writer_clear_formatting",
     "description": "Remove direct character/paragraph formatting (reset to the underlying style) from text matching 'search', or a body-paragraph range ('start'/'count', default all).",
     "inputSchema": _schema({"search": dict(_STR, description="clear matched text; omit for paragraph range"),
                             "match_case": _BOOL,
                             "start": dict(_INT, description="first paragraph index (0-based)"),
                             "count": dict(_INT, description="how many paragraphs (default: to end)")})},
    {"name": "writer_replace_image",
     "description": "Replace an existing image by 'name': swap its graphic (new 'path') and/or resize it (width_mm/height_mm) in place — e.g. update a logo without rebuilding. Use writer_list_objects to find image names.",
     "inputSchema": _schema({"name": _STR,
                             "path": dict(_STR, description="new image file (omit to only resize)"),
                             "width_mm": _NUM, "height_mm": _NUM},
                            ["name"])},
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
    {"name": "writer_format_document",
     "description": "Make a Writer document presentable in ONE call: base font and size (all scripts, so Arabic/CTL takes effect), line spacing and page margins. Presets: report (sans 11pt, 20mm, 1.15), essay (serif 12pt, 1in, double), letter (serif 12pt, 25mm, single).",
     "inputSchema": _schema({"preset": dict(_STR, enum=["report", "essay", "letter"]),
                             "font_name": dict(_STR, description="override the preset font"),
                             "font_size": dict(_NUM, description="override the preset size (pt)")})},
]

register(globals(), TOOL_DEFS,
         basic=['writer_apply_style', 'writer_format_document', 'writer_format_text', 'writer_insert_image'],
         read_only=['writer_list_objects'])
