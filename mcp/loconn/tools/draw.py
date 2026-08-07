# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Draw tools — vector drawings.

A separate surface from Impress, but the shape/text/image primitives are the
same drawing model (_DRAW_SHAPES, _place_shape, GraphicProvider — all shared
from core). Pages are addressed by a 1-based index.
"""
import os

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _INT, _NUM)  # noqa: F401
from ..registry import register


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


# --------------------------------------------------------------------------- #
# Schemas — pages addressed by 1-based index
# --------------------------------------------------------------------------- #

TOOL_DEFS = [
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


register(globals(), TOOL_DEFS,
         basic=['draw_overview', 'draw_read_page', 'draw_insert_shape',
                'draw_insert_text_box', 'draw_insert_image',
                'draw_insert_connector'],
         read_only=['draw_overview', 'draw_read_page'])
