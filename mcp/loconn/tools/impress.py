# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Impress tools — presentations.

Slides are addressed by a 1-BASED index everywhere ("slide 3" = the 3rd slide).
Placeholders resolve by service where LibreOffice reports it reliably (title,
body) and by structure for notes. Layout ints and the placeholder model were
measured on LO 25.2 — see the "Phase 0 findings" in docs/PLAN-IMPRESS-MVP.md.
"""
import os

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM)  # noqa: F401
from ..registry import register


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


def _count_animations(page):
    """Number of per-object animation effects attached to the slide's main
    sequence (each ParallelTimeContainer child holding a targeted node)."""
    try:
        seq = page.AnimationNode
    except Exception:
        return 0
    n = 0
    try:
        for grp in seq.createEnumeration():
            for eff in grp.createEnumeration():
                if getattr(eff, "Target", None) is not None:
                    n += 1
    except Exception:
        pass
    return n


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
            "notes": notes.getString() if notes else "",
            "animations": _count_animations(page)}


def tool_impress_set_notes(args):
    page = _impress_slide(_impress_pages(), args["slide"])
    shp = _ph_notes(page)
    if shp is None:
        raise RuntimeError("slide %s has no speaker-notes area" % args["slide"])
    shp.setString(str(args["text"]))
    return {"slide": int(args["slide"]), "notes": args["text"]}


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


# per-object animation triggers -> com.sun.star.presentation.EffectNodeType names
_ANIM_TRIGGERS = {"on_click": "ON_CLICK", "with_previous": "WITH_PREVIOUS",
                  "after_previous": "AFTER_PREVIOUS"}
# effects: 'appear' (instant) + the shared transition vocabulary as a reveal
_ANIM_EFFECTS = ["appear"] + [k for k, v in _IMPRESS_TRANSITIONS.items() if v]


def tool_impress_add_animation(args):
    """Attach a per-object animation to a shape. Animation nodes are only
    creatable through the component-context service manager (not the document
    factory) on LO 25.2 — hence smgr.createInstanceWithContext below."""
    import uno
    from com.sun.star.animations.AnimationFill import HOLD
    doc = _require_impress()
    page = _impress_slide(doc.getDrawPages(), args["slide"])
    idx = int(args["shape"]) - 1
    if idx < 0 or idx >= page.getCount():
        raise RuntimeError("shape %r is out of range 1..%d"
                           % (args["shape"], page.getCount()))
    shape = page.getByIndex(idx)
    effect = str(args.get("effect", "appear")).lower()
    if effect not in _ANIM_EFFECTS:
        raise RuntimeError("effect must be one of %s" % sorted(_ANIM_EFFECTS))
    trigger = str(args.get("trigger", "on_click")).lower()
    if trigger not in _ANIM_TRIGGERS:
        raise RuntimeError("trigger must be one of %s" % sorted(_ANIM_TRIGGERS))
    duration = float(args.get("duration", 0.5))
    state = _connect()
    smgr, ctx = state["smgr"], state["ctx"]

    def mk(name):
        return smgr.createInstanceWithContext("com.sun.star.animations." + name, ctx)

    par = mk("ParallelTimeContainer")
    try:
        nv = uno.createUnoStruct("com.sun.star.beans.NamedValue")
        nv.Name = "node-type"
        nv.Value = uno.getConstantByName(
            "com.sun.star.presentation.EffectNodeType." + _ANIM_TRIGGERS[trigger])
        par.UserData = (nv,)
    except Exception:
        pass
    if effect == "appear":
        node = mk("AnimateSet")
        node.AttributeName = "Visibility"
        node.To = uno.Any("boolean", True)
    else:
        tname, sname = _IMPRESS_TRANSITIONS[effect]
        node = mk("TransitionFilter")
        node.Transition = uno.getConstantByName(
            "com.sun.star.animations.TransitionType." + tname)
        node.Subtype = uno.getConstantByName(
            "com.sun.star.animations.TransitionSubType." + sname)
        node.Duration = duration
    node.Target = shape
    node.Fill = HOLD
    par.appendChild(node)
    page.AnimationNode.appendChild(par)
    return {"slide": int(args["slide"]), "shape": int(args["shape"]),
            "effect": effect, "trigger": trigger,
            "animations": _count_animations(page)}


# --------------------------------------------------------------------------- #
# Schemas — slides addressed by 1-based index
# --------------------------------------------------------------------------- #

TOOL_DEFS = [
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
    {"name": "impress_add_animation",
     "description": "Animate a shape on slide 'slide' (1-based). 'shape' is the 1-based shape index (see impress_read_slide). 'effect': appear, fade, wipe, push, cover, uncover, dissolve, wheel, or cut. 'trigger': on_click (default), with_previous, or after_previous. 'duration' in seconds. This is a per-object build-in animation — something .pptx file writers cannot do.",
     "inputSchema": _schema({"slide": _INT, "shape": _INT,
                             "effect": dict(_STR, enum=sorted(_ANIM_EFFECTS)),
                             "trigger": dict(_STR, enum=sorted(_ANIM_TRIGGERS)),
                             "duration": _NUM},
                            ["slide", "shape"])},
]


register(globals(), TOOL_DEFS,
         basic=['impress_overview', 'impress_read_slide', 'impress_add_slide',
                'impress_set_title', 'impress_set_content', 'impress_set_notes',
                'impress_insert_image', 'impress_insert_shape',
                'impress_set_transition', 'impress_export_slides',
                'impress_insert_table', 'impress_insert_chart',
                'impress_set_background', 'impress_add_animation'],
         read_only=['impress_overview', 'impress_read_slide',
                    'impress_export_slides', 'impress_slideshow'])
