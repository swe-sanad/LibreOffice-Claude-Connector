# Impress MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 13-tool `impress_*` family to the LibreOffice MCP server so an agent can build a presentation end-to-end (create → structure → content → notes → images/shapes → read-back → export PDF) against a live LibreOffice.

**Architecture:** New `impress_*` tool family in `mcp/libreoffice_mcp.py`, gated by a new `_require_impress()`, addressing slides by 1-based index and placeholders by UNO service. Slide/shape UNO helpers live in a new Impress section of `src/uno_bridge.py`. Creating and exporting presentations reuse the existing `create_document` / `export_document` tools via one factory-URL and one export-filter row. A Phase-0 spike (`scripts/spike_impress.py`) probes the live placeholder/layout model first and records confirmed constants that the tool tasks consume.

**Tech Stack:** Python (LibreOffice bundled 3.10, target 3.8 syntax), UNO API, stdlib only. Tests: `unittest` (offline) + the isolated-profile UNO integration harness (`scripts/run_integration.ps1`).

## Global Constraints

- **Stdlib only** — no `requests`, no SDK, no compiled wheels. Must run under LibreOffice's bundled `python.exe`.
- **Target Python 3.8 syntax** (LO 24.8=3.9 … 25.8=3.11). No `match`, no `|` union types, no walrus-in-comprehension cleverness.
- **`src/` is the single source of truth.** Never hand-edit `dist/`.
- **Every tool registers in BOTH `TOOLS` (line ~7326) and `TOOL_DEFS` (line ~7558)** — a test enforces parity.
- **Read-only tools go in `_NO_UNDO`** (line ~183) or the undo-context test fails.
- **Advertised tools go in the `_BASIC_TOOLS` frozenset** (line ~8639); everything else is reachable via `dispatch` / `LO_TOOLS=full`.
- **Handlers return a plain dict; raise on failure** (classified by `_classify_error`). No custom envelope.
- **Positions/sizes are 1/100 mm; assert with ±2 tolerance** (LO round-trips through twips).
- **All Python runs under** `C:\Program Files\LibreOffice\program\python.exe` (it has `uno`).
- **Design source of truth:** [`docs/PLAN-IMPRESS-MVP.md`](../../PLAN-IMPRESS-MVP.md). Keep it in sync.

---

### Task 0: Live UNO probe — discover and record the placeholder/layout model

**Why first:** `PLAN-IMPRESS-BASE-DRAW.md` flags the placeholder model as *the* genuine unknown (same class as the Writer table-anchor and Calc force-text-marker bugs). Every later task's exact constants (`_IMPRESS_LAYOUTS`, placeholder service names, notes-page access, bullet levels, image insert, duplicate/move availability) come from this probe. Do NOT guess these — measure them.

**Files:**
- Create: `scripts/spike_impress.py`
- Modify: `docs/PLAN-IMPRESS-MVP.md` (add a "## Phase 0 findings" section with the measured values)

**Interfaces:**
- Produces (for all later tasks): confirmed values for
  - `_IMPRESS_LAYOUTS: Dict[str,int]` — friendly name → `DrawPage.Layout` int
  - `TITLE_SVC`, `OUTLINE_SVC`, `SUBTITLE_SVC`, `NOTES_SVC` — placeholder service strings
  - how to reach the notes text shape from a slide
  - whether per-paragraph `NumberingLevel` sets bullet indent in the outline shape
  - whether `doc.duplicate(page)` exists and whether any page-reorder API exists

- [ ] **Step 1: Write the probe script**

```python
# scripts/spike_impress.py — throwaway discovery, run under LO python against a live socket office.
# Usage: powershell scripts\start_office_socket.ps1   (then)
#        & "C:\Program Files\LibreOffice\program\python.exe" scripts\spike_impress.py
import uno
from com.sun.star.beans import PropertyValue

def _ctx():
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    return resolver.resolve(
        "uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext")

def main():
    ctx = _ctx()
    smgr = ctx.ServiceManager
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    pages = doc.getDrawPages()
    page = pages.getByIndex(0)

    # 1. Which services appear for each Layout int?
    for layout in range(0, 21):
        page.Layout = layout
        svcs = []
        for i in range(page.getCount()):
            shp = page.getByIndex(i)
            for s in ("TitleTextShape", "OutlineTextShape", "SubtitleTextShape", "NotesTextShape"):
                full = "com.sun.star.presentation." + s
                if shp.supportsService(full):
                    svcs.append(s)
        print("Layout %2d -> %s" % (layout, svcs))

    # 2. Notes page access + notes shape service
    page.Layout = 1
    try:
        notes = page.getNotesPage()
        nsvc = [notes.getByIndex(i).supportsService("com.sun.star.presentation.NotesTextShape")
                for i in range(notes.getCount())]
        print("getNotesPage count=%d notes-shape-present=%s" % (notes.getCount(), any(nsvc)))
    except Exception as e:
        print("getNotesPage FAILED:", e)

    # 3. Bullet levels in the outline shape
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        if shp.supportsService("com.sun.star.presentation.OutlineTextShape"):
            t = shp.getText()
            t.setString("level0")
            cur = t.createTextCursor()
            from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
            t.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
            t.insertString(cur, "level1", False)
            paras = list(t.createEnumeration())  # note whether this enumerates paragraphs
            try:
                paras[1].NumberingLevel = 1
                print("NumberingLevel set OK; paras=%d" % len(paras))
            except Exception as e:
                print("NumberingLevel FAILED:", e)
            break

    # 4. Duplicate + reorder availability
    print("doc has duplicate():", hasattr(doc, "duplicate"))
    print("pages has moveByIndex():", hasattr(pages, "moveByIndex"))
    try:
        dup = doc.duplicate(page); print("duplicate() OK, count now", pages.getCount())
    except Exception as e:
        print("duplicate() FAILED:", e)

    doc.close(False)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the probe against a live office**

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_office_socket.ps1
```
Then:
```bash
"C:\Program Files\LibreOffice\program\python.exe" scripts\spike_impress.py
```
Expected: prints the Layout→services table, notes access result, NumberingLevel result, duplicate/move availability.

- [ ] **Step 3: Record findings in `docs/PLAN-IMPRESS-MVP.md`**

Add a `## Phase 0 findings` section with the concrete measured values, e.g.:
```markdown
## Phase 0 findings (LO 25.2, measured 2026-08-07)
- Layout ints: title_subtitle=0, title_content=1, two_content=3, title_only=20, blank=20 -> CORRECTED to <measured>
- Placeholder services confirmed: TitleTextShape, OutlineTextShape, SubtitleTextShape, NotesTextShape
- Notes: page.getNotesPage() -> NotesTextShape present = <yes/no>
- Bullet levels: per-paragraph NumberingLevel works = <yes/no>; paragraph enumeration = <yes/no>
- duplicate(page): <available/not>; page reorder API: <moveByIndex? / none -> drop impress_move_slide>
```

- [ ] **Step 4: Commit**

```bash
git add scripts/spike_impress.py docs/PLAN-IMPRESS-MVP.md
git commit -m "chore(impress): live UNO probe of placeholder/layout model (Phase 0)"
```

> **Decision gate:** if the probe shows no clean page-reorder API, strike `impress_move_slide` from Task 6 and this plan (YAGNI). If `NumberingLevel` doesn't work, Task 3's `impress_set_content` records bullet levels via the confirmed mechanism instead.

---

### Task 1: Wiring — doc-type detection, factory URL, export filter, `_require_impress()`

**Files:**
- Modify: `src/uno_bridge.py` (add Impress section after the Writer block, ~line 275)
- Modify: `mcp/libreoffice_mcp.py` (`_FACTORY_URLS` ~891, enum ~7582, `_FILTERS` ~895, add `_require_impress` near `_require_writer` ~467, add `_IMPRESS_LAYOUTS` + placeholder-service constants using Task-0 values)
- Test: `tests/test_everyday_surface.py` (extend)

**Interfaces:**
- Produces: `ub.is_impress(doc) -> bool`; `_require_impress() -> doc`; module constants `_IMPRESS_LAYOUTS`, `TITLE_SVC`, `OUTLINE_SVC`, `SUBTITLE_SVC`, `NOTES_SVC`; `create_document` accepts `type="impress"`; `export_document` supports Impress PDF.

- [ ] **Step 1: Write the failing offline test**

```python
# tests/test_everyday_surface.py
def test_create_document_accepts_impress(self):
    import mcp.libreoffice_mcp as m
    enum = next(d for d in m.TOOL_DEFS if d["name"] == "create_document") \
        ["inputSchema"]["properties"]["type"]["enum"]
    self.assertIn("impress", enum)

def test_impress_factory_url_registered(self):
    import mcp.libreoffice_mcp as m
    self.assertEqual(m._FACTORY_URLS["impress"], "private:factory/simpress")
```

- [ ] **Step 2: Run to verify it fails**

Run: `& "C:\Program Files\LibreOffice\program\python.exe" -m unittest tests.test_everyday_surface -v`
Expected: FAIL — `impress` not in enum / KeyError.

- [ ] **Step 3: Add `is_impress` to `src/uno_bridge.py`**

```python
# after WRITER_DOC_SERVICE block
IMPRESS_DOC_SERVICE = "com.sun.star.presentation.PresentationDocument"

def is_impress(doc):
    try:
        return bool(doc) and doc.supportsService(IMPRESS_DOC_SERVICE)
    except Exception:
        return False
```

- [ ] **Step 4: Wire the server**

```python
# mcp/libreoffice_mcp.py
# _FACTORY_URLS (~891):
_FACTORY_URLS = {
    "calc": "private:factory/scalc",
    "writer": "private:factory/swriter",
    "impress": "private:factory/simpress",
}
# create_document type enum (~7582): add "impress"
# _FILTERS (~895): add Impress rows
#   ("impress", "pdf"): "impress_pdf_Export",
#   ("impress", "odp"): "impress8",
#   ("impress", "pptx"): "Impress MS PowerPoint 2007 XML",

# Placeholder + layout constants (values CONFIRMED in Task 0):
TITLE_SVC    = "com.sun.star.presentation.TitleTextShape"
OUTLINE_SVC  = "com.sun.star.presentation.OutlineTextShape"
SUBTITLE_SVC = "com.sun.star.presentation.SubtitleTextShape"
NOTES_SVC    = "com.sun.star.presentation.NotesTextShape"
_IMPRESS_LAYOUTS = {          # friendly name -> DrawPage.Layout int (Task 0 values)
    "title_subtitle": 0, "title_content": 1, "two_content": 3,
    "title_only": 20, "blank": 20,
}

def _require_impress():
    doc = _current_doc()
    if not ub.is_impress(doc):
        raise ValueError("The active document is not a presentation (Impress).")
    return doc
```

- [ ] **Step 5: Run to verify it passes**

Run: `& "C:\Program Files\LibreOffice\program\python.exe" -m unittest tests.test_everyday_surface -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/uno_bridge.py mcp/libreoffice_mcp.py tests/test_everyday_surface.py
git commit -m "feat(impress): wiring - is_impress, simpress factory, PDF filter, _require_impress"
```

---

### Task 2: `impress_add_slide` + `impress_overview` (create + read the deck)

**Files:**
- Modify: `mcp/libreoffice_mcp.py` (add both handlers, `TOOLS`, `TOOL_DEFS`, `_BASIC_TOOLS`, `_NO_UNDO` for overview)
- Test: `tests/integration/test_impress_uno.py` (create), `tests/test_everyday_surface.py` (registry)

**Interfaces:**
- Consumes: `_require_impress`, `_IMPRESS_LAYOUTS` (Task 1).
- Produces: `tool_impress_add_slide(args) -> {"slide": int, "count": int}`; `tool_impress_overview(args) -> {"count": int, "slides": [{"index","layout","title","text_len","has_notes"}]}`. Slide args are 1-based.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_impress_uno.py
import os, unittest
import mcp.libreoffice_mcp as m

class ImpressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = m._desktop().loadComponentFromURL(
            "private:factory/simpress", "_blank", 0, ())
    @classmethod
    def tearDownClass(cls):
        cls.doc.close(False)

    def test_add_slide_and_overview(self):
        before = m.tool_impress_overview({})["count"]
        r = m.tool_impress_add_slide({"layout": "title_content"})
        self.assertEqual(r["count"], before + 1)
        ov = m.tool_impress_overview({})
        self.assertEqual(ov["count"], before + 1)
        self.assertEqual(ov["slides"][-1]["layout"], "title_content")
```

- [ ] **Step 2: Run to verify it fails**

Run: `powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_impress_uno.py`
Expected: FAIL — `tool_impress_add_slide` not defined.

- [ ] **Step 3: Implement both handlers**

```python
def _pages():
    return _require_impress().getDrawPages()

def _slide(pages, one_based):
    n = pages.getCount()
    i = one_based - 1
    if i < 0 or i >= n:
        raise ValueError("slide %d out of range 1..%d" % (one_based, n))
    return pages.getByIndex(i)

def _layout_name(page):
    for name, val in _IMPRESS_LAYOUTS.items():
        if page.Layout == val:
            return name
    return "custom(%d)" % page.Layout

def _placeholder(page, svc):
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        if shp.supportsService(svc):
            return shp
    return None

def tool_impress_add_slide(args):
    pages = _pages()
    count = pages.getCount()
    pos = args.get("slide")
    idx = count if pos is None else max(0, min(count, int(pos) - 1))
    page = pages.insertNewByIndex(idx)
    layout = args.get("layout", "title_content")
    if layout not in _IMPRESS_LAYOUTS:
        raise ValueError("unknown layout %r; choose one of %s"
                         % (layout, sorted(_IMPRESS_LAYOUTS)))
    page.Layout = _IMPRESS_LAYOUTS[layout]
    return {"slide": idx + 1, "count": pages.getCount()}

def tool_impress_overview(args):
    pages = _pages()
    out = []
    for i in range(pages.getCount()):
        page = pages.getByIndex(i)
        title = _placeholder(page, TITLE_SVC)
        body = _placeholder(page, OUTLINE_SVC)
        notes = _placeholder(page.getNotesPage(), NOTES_SVC) \
            if hasattr(page, "getNotesPage") else None
        out.append({
            "index": i + 1,
            "layout": _layout_name(page),
            "title": title.getString() if title else "",
            "text_len": len(body.getString()) if body else 0,
            "has_notes": bool(notes and notes.getString().strip()),
        })
    return {"count": pages.getCount(), "slides": out}
```
Register: `TOOLS["impress_add_slide"] = tool_impress_add_slide`, `TOOLS["impress_overview"] = tool_impress_overview`; add both to `TOOL_DEFS` (schema: add_slide `{"slide": _INT, "layout": {"type":"string","enum":[...]}}` none required; overview `_schema({}, [])`); add both names to `_BASIC_TOOLS`; add `impress_overview` to `_NO_UNDO`.

- [ ] **Step 4: Run to verify it passes**

Run: `powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_impress_uno.py`
Expected: PASS.

- [ ] **Step 5: Registry test + run offline suite**

Add to `tests/test_everyday_surface.py`: assert both names appear in `TOOLS` and `TOOL_DEFS`, both in `_BASIC_TOOLS`, `impress_overview` in `_NO_UNDO`.
Run: `& "C:\Program Files\LibreOffice\program\python.exe" -m unittest tests.test_everyday_surface -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp/libreoffice_mcp.py tests/integration/test_impress_uno.py tests/test_everyday_surface.py
git commit -m "feat(impress): impress_add_slide + impress_overview"
```

---

### Task 3: `impress_set_title` + `impress_set_content` + `impress_read_slide`

**Files:**
- Modify: `mcp/libreoffice_mcp.py`
- Test: `tests/integration/test_impress_uno.py`, `tests/test_everyday_surface.py`

**Interfaces:**
- Consumes: `_slide`, `_placeholder`, `TITLE_SVC`, `OUTLINE_SVC`, `NOTES_SVC` (Task 2).
- Produces: `tool_impress_set_title({"slide","text"}) -> {"slide","title"}`; `tool_impress_set_content({"slide","bullets":[{"text","level"?}]}) -> {"slide","bullets":int}`; `tool_impress_read_slide({"slide"}) -> {"index","layout","title","bullets":[...],"shapes":[...],"notes"}`.

- [ ] **Step 1: Write the failing integration test**

```python
def test_title_content_readback(self):
    m.tool_impress_add_slide({"layout": "title_content"})
    n = m.tool_impress_overview({})["count"]
    m.tool_impress_set_title({"slide": n, "text": "Quarterly Review"})
    m.tool_impress_set_content({"slide": n, "bullets": [
        {"text": "Revenue up 12%"},
        {"text": "APAC detail", "level": 1},
        {"text": "Risks"},
    ]})
    rs = m.tool_impress_read_slide({"slide": n})
    self.assertEqual(rs["title"], "Quarterly Review")
    self.assertEqual([b["text"] for b in rs["bullets"]],
                     ["Revenue up 12%", "APAC detail", "Risks"])
    self.assertEqual(rs["bullets"][1]["level"], 1)
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`tool_impress_set_title` undefined).

- [ ] **Step 3: Implement**

```python
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK  # module top

def tool_impress_set_title(args):
    page = _slide(_pages(), int(args["slide"]))
    shp = _placeholder(page, TITLE_SVC)
    if shp is None:
        raise ValueError("slide %s has no title placeholder; set a layout with a title"
                         % args["slide"])
    shp.setString(args["text"])
    return {"slide": int(args["slide"]), "title": args["text"]}

def tool_impress_set_content(args):
    page = _slide(_pages(), int(args["slide"]))
    shp = _placeholder(page, OUTLINE_SVC)
    if shp is None:
        raise ValueError("slide %s has no content placeholder; use layout 'title_content'"
                         % args["slide"])
    bullets = args["bullets"] or []
    text = shp.getText()
    text.setString("")
    cur = text.createTextCursor()
    for i, b in enumerate(bullets):
        if i:
            text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
        text.insertString(cur, b.get("text", ""), False)
    # apply per-paragraph levels (mechanism confirmed in Task 0)
    paras = list(text.createEnumeration())
    for b, para in zip(bullets, paras):
        lvl = int(b.get("level", 0))
        try:
            para.NumberingLevel = lvl
        except Exception:
            pass
    return {"slide": int(args["slide"]), "bullets": len(bullets)}

def tool_impress_read_slide(args):
    page = _slide(_pages(), int(args["slide"]))
    title = _placeholder(page, TITLE_SVC)
    body = _placeholder(page, OUTLINE_SVC)
    bullets = []
    if body:
        for para in body.getText().createEnumeration():
            bullets.append({"text": para.getString(),
                            "level": getattr(para, "NumberingLevel", 0)})
    shapes = []
    for i in range(page.getCount()):
        shp = page.getByIndex(i)
        if not (shp.supportsService(TITLE_SVC) or shp.supportsService(OUTLINE_SVC)):
            shapes.append(shp.Name)
    notes_shp = _placeholder(page.getNotesPage(), NOTES_SVC) \
        if hasattr(page, "getNotesPage") else None
    return {
        "index": int(args["slide"]),
        "layout": _layout_name(page),
        "title": title.getString() if title else "",
        "bullets": bullets,
        "shapes": shapes,
        "notes": notes_shp.getString() if notes_shp else "",
    }
```
Register all three; `impress_set_title`, `impress_set_content` → `_BASIC_TOOLS`; `impress_read_slide` → `_BASIC_TOOLS` **and** `_NO_UNDO`. Schemas: set_title `_schema({"slide":_INT,"text":_STR},["slide","text"])`; set_content `_schema({"slide":_INT,"bullets":{"type":"array","items":{"type":"object"}}},["slide","bullets"])`; read_slide `_schema({"slide":_INT},["slide"])`.

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Registry test** for the three names; run offline suite → PASS.

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(impress): set_title, set_content (bullet levels), read_slide"
```

---

### Task 4: `impress_set_notes` (speaker notes — competitor gap)

**Files:** Modify `mcp/libreoffice_mcp.py`; test `tests/integration/test_impress_uno.py`.

**Interfaces:**
- Consumes: `_slide`, `_placeholder`, `NOTES_SVC`.
- Produces: `tool_impress_set_notes({"slide","text"}) -> {"slide","notes"}`.

- [ ] **Step 1: Failing test**

```python
def test_notes_roundtrip(self):
    m.tool_impress_add_slide({"layout": "title_only"})
    n = m.tool_impress_overview({})["count"]
    m.tool_impress_set_notes({"slide": n, "text": "Pause for questions here."})
    self.assertTrue(m.tool_impress_overview({})["slides"][-1]["has_notes"])
    self.assertEqual(m.tool_impress_read_slide({"slide": n})["notes"],
                     "Pause for questions here.")
```

- [ ] **Step 2: Run — FAIL** (`tool_impress_set_notes` undefined).

- [ ] **Step 3: Implement**

```python
def tool_impress_set_notes(args):
    page = _slide(_pages(), int(args["slide"]))
    if not hasattr(page, "getNotesPage"):
        raise ValueError("this document has no notes pages")
    shp = _placeholder(page.getNotesPage(), NOTES_SVC)
    if shp is None:
        raise ValueError("slide %s has no notes placeholder" % args["slide"])
    shp.setString(args["text"])
    return {"slide": int(args["slide"]), "notes": args["text"]}
```
Register; add to `_BASIC_TOOLS`. Schema `_schema({"slide":_INT,"text":_STR},["slide","text"])`.

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Registry test; offline suite → PASS.**
- [ ] **Step 6: Commit** — `git commit -am "feat(impress): impress_set_notes (speaker notes)"`

---

### Task 5: `impress_insert_image` + `impress_insert_shape` + `impress_insert_text_box`

**Files:** Modify `mcp/libreoffice_mcp.py`; test `tests/integration/test_impress_uno.py`.

**Interfaces:**
- Consumes: `_slide`, `_require_impress`.
- Produces: `tool_impress_insert_image({"slide","path","x"?,"y"?,"w"?,"h"?}) -> {"slide","name"}`; `tool_impress_insert_shape({"slide","kind","x","y","w","h","text"?}) -> {"slide","name"}`; `tool_impress_insert_text_box({"slide","text","x","y","w","h"}) -> {"slide","name"}`.

- [ ] **Step 1: Failing test** (image + shape; text_box asserted via `read_slide` shapes count)

```python
def test_insert_image_and_shape(self):
    m.tool_impress_add_slide({"layout": "blank"})
    n = m.tool_impress_overview({})["count"]
    png = os.path.join(os.path.dirname(__file__), "..", "..", "ext", "icons", "claude_26.png")
    ri = m.tool_impress_insert_image({"slide": n, "path": os.path.abspath(png),
                                      "x": 1000, "y": 1000, "w": 3000, "h": 3000})
    self.assertTrue(ri["name"])
    m.tool_impress_insert_shape({"slide": n, "kind": "rectangle",
                                 "x": 5000, "y": 1000, "w": 4000, "h": 2000, "text": "Box"})
    self.assertGreaterEqual(len(m.tool_impress_read_slide({"slide": n})["shapes"]), 2)
```
(Use an existing bundled PNG; adjust the path to a real icon in `ext/icons/`.)

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
from com.sun.star.awt import Point, Size  # module top

_SHAPE_KINDS = {
    "rectangle": "com.sun.star.drawing.RectangleShape",
    "ellipse":   "com.sun.star.drawing.EllipseShape",
    "line":      "com.sun.star.drawing.LineShape",
    "text":      "com.sun.star.drawing.TextShape",
}

def _pos_size(shape, x, y, w, h):
    shape.Position = Point(int(x), int(y))
    shape.Size = Size(int(w), int(h))

def tool_impress_insert_image(args):
    doc = _require_impress()
    page = _slide(doc.getDrawPages(), int(args["slide"]))
    url = ub.resolve_url(args["path"])  # existing path->file: URL helper
    gp = _ctx().ServiceManager.createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", _ctx())
    graphic = gp.queryGraphic((_prop("URL", url),))
    shape = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
    page.add(shape)
    shape.Graphic = graphic
    _pos_size(shape, args.get("x", 0), args.get("y", 0),
              args.get("w", 6000), args.get("h", 4000))
    return {"slide": int(args["slide"]), "name": shape.Name}

def tool_impress_insert_shape(args):
    doc = _require_impress()
    page = _slide(doc.getDrawPages(), int(args["slide"]))
    kind = args.get("kind", "rectangle")
    if kind not in _SHAPE_KINDS:
        raise ValueError("unknown shape kind %r; choose %s" % (kind, sorted(_SHAPE_KINDS)))
    shape = doc.createInstance(_SHAPE_KINDS[kind])
    page.add(shape)
    _pos_size(shape, args["x"], args["y"], args["w"], args["h"])
    if args.get("text"):
        shape.setString(args["text"])
    return {"slide": int(args["slide"]), "name": shape.Name}

def tool_impress_insert_text_box(args):
    doc = _require_impress()
    page = _slide(doc.getDrawPages(), int(args["slide"]))
    shape = doc.createInstance("com.sun.star.drawing.TextShape")
    page.add(shape)
    _pos_size(shape, args["x"], args["y"], args["w"], args["h"])
    shape.TextAutoGrowHeight = True
    shape.setString(args["text"])
    return {"slide": int(args["slide"]), "name": shape.Name}
```
`_prop(name, value)` = existing `PropertyValue` helper (reuse the one used elsewhere; grep for `PropertyValue(` usage). `_ctx()` = existing cached context accessor (grep for how `_desktop()` gets its context — reuse that, don't add a new connector).
Register all three; `impress_insert_image`, `impress_insert_shape` → `_BASIC_TOOLS`; `impress_insert_text_box` stays full-only. Schemas require the geometry args as noted.

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Registry test; offline suite → PASS.**
- [ ] **Step 6: Commit** — `git commit -am "feat(impress): insert_image, insert_shape, insert_text_box"`

---

### Task 6: Slide management — `impress_set_layout`, `impress_delete_slide`, `impress_duplicate_slide`, (`impress_move_slide` if Task 0 allows)

**Files:** Modify `mcp/libreoffice_mcp.py`; test `tests/integration/test_impress_uno.py`.

**Interfaces:**
- Consumes: `_pages`, `_slide`, `_IMPRESS_LAYOUTS`.
- Produces: `tool_impress_set_layout({"slide","layout"}) -> {"slide","layout"}`; `tool_impress_delete_slide({"slide"}) -> {"count"}`; `tool_impress_duplicate_slide({"slide"}) -> {"slide","count"}`; `tool_impress_move_slide({"slide","to"}) -> {"count"}` (only if reorder API exists).

- [ ] **Step 1: Failing test**

```python
def test_slide_management(self):
    m.tool_impress_add_slide({"layout": "blank"})
    before = m.tool_impress_overview({})["count"]
    m.tool_impress_duplicate_slide({"slide": before})
    self.assertEqual(m.tool_impress_overview({})["count"], before + 1)
    m.tool_impress_set_layout({"slide": before, "layout": "title_only"})
    self.assertEqual(m.tool_impress_read_slide({"slide": before})["layout"], "title_only")
    m.tool_impress_delete_slide({"slide": before + 1})
    self.assertEqual(m.tool_impress_overview({})["count"], before)
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
def tool_impress_set_layout(args):
    page = _slide(_pages(), int(args["slide"]))
    layout = args["layout"]
    if layout not in _IMPRESS_LAYOUTS:
        raise ValueError("unknown layout %r" % layout)
    page.Layout = _IMPRESS_LAYOUTS[layout]
    return {"slide": int(args["slide"]), "layout": layout}

def tool_impress_delete_slide(args):
    pages = _pages()
    page = _slide(pages, int(args["slide"]))
    pages.remove(page)
    return {"count": pages.getCount()}

def tool_impress_duplicate_slide(args):
    doc = _require_impress()
    page = _slide(doc.getDrawPages(), int(args["slide"]))
    doc.duplicate(page)   # XDrawPageDuplicator; confirmed available in Task 0
    return {"slide": int(args["slide"]), "count": doc.getDrawPages().getCount()}
```
`impress_move_slide`: implement ONLY with the reorder API confirmed in Task 0; if none exists, omit it (and remove its row from `docs/PLAN-IMPRESS-MVP.md`).
Register the implemented tools (all full-only, none in `_BASIC_TOOLS`). Schemas: set_layout `_schema({"slide":_INT,"layout":_STR},["slide","layout"])`; delete/duplicate `_schema({"slide":_INT},["slide"])`.

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Registry test; offline suite → PASS.**
- [ ] **Step 6: Commit** — `git commit -am "feat(impress): set_layout, delete_slide, duplicate_slide"`

---

### Task 7: End-to-end export test + docs + CHANGELOG

**Files:**
- Test: `tests/integration/test_impress_uno.py` (add end-to-end)
- Modify: `docs/MCP-TOOLS.md`, `docs/CHANGELOG.md`, `docs/PLAN-IMPRESS-BASE-DRAW.md` (mark Impress in progress)

**Interfaces:** Consumes the whole family + existing `tool_export_document`.

- [ ] **Step 1: Write the end-to-end test**

```python
def test_build_deck_and_export_pdf(self):
    # fresh deck: title slide, bulleted content, image+notes slide
    m.tool_impress_set_layout({"slide": 1, "layout": "title_subtitle"})
    m.tool_impress_set_title({"slide": 1, "text": "SWE Pioneers"})
    m.tool_impress_add_slide({"layout": "title_content"})
    m.tool_impress_set_title({"slide": 2, "text": "Agenda"})
    m.tool_impress_set_content({"slide": 2, "bullets": [
        {"text": "Intro"}, {"text": "Detail", "level": 1}, {"text": "Wrap"}]})
    m.tool_impress_set_notes({"slide": 2, "text": "Keep it to five minutes."})
    out = os.path.join(os.environ.get("TEMP", "."), "impress_mvp_test.pdf")
    if os.path.exists(out):
        os.remove(out)
    m.tool_export_document({"path": out, "format": "pdf"})
    self.assertTrue(os.path.exists(out) and os.path.getsize(out) > 0)
```
(Confirm the exact `export_document` arg names by grepping its schema; adjust `path`/`format` to match.)

- [ ] **Step 2: Run — PASS** (all prior tasks landed).

Run: `powershell -ExecutionPolicy Bypass -File scripts\run_integration.ps1 -Test tests\integration\test_impress_uno.py`

- [ ] **Step 3: Update docs**

- `docs/MCP-TOOLS.md`: add an "Impress" section listing the 13 tools (mark the 8 advertised).
- `docs/CHANGELOG.md`: add under Unreleased — "feat(impress): impress_* presentation family (13 tools) — create/structure/content/notes/images/shapes/read-back/PDF export".
- `docs/PLAN-IMPRESS-BASE-DRAW.md`: at the top of the Impress priority section, add "**In progress → see [PLAN-IMPRESS-MVP.md](PLAN-IMPRESS-MVP.md).**"

- [ ] **Step 4: Full offline suite + protocol smoke**

```bash
"C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py"
"C:\Program Files\LibreOffice\program\python.exe" mcp\test_mcp_protocol.py
```
Expected: all green; default advertises 32 + 8 = the new basic count; `LO_TOOLS=full` advertises 174 + 13.

- [ ] **Step 5: Commit**

```bash
git commit -am "test(impress): end-to-end deck + PDF export; docs + changelog"
```

---

## Self-Review

**Spec coverage:** create (`create_document`+impress, Task 1) ✓ · add/layout/delete/duplicate/move slides (Tasks 2,6) ✓ · title/bullets (Task 3) ✓ · notes (Task 4) ✓ · image/shape/text-box (Task 5) ✓ · read-back (`overview`,`read_slide`, Tasks 2,3) ✓ · PDF export (`export_document`+filter, Tasks 1,7) ✓ · 1-based addressing (all) ✓ · service-based placeholders (`_placeholder`, Task 2) ✓ · Phase-0 probe (Task 0) ✓ · offline registry/tier tests + live integration (every task + Task 7) ✓. All 13 tools have a task.

**Placeholder scan:** No "TBD/TODO/handle edge cases". Two calibration points are deliberate and named, not vague: Task-0-confirmed layout ints (a calibration knob, best-known defaults given) and the `NumberingLevel` mechanism (probed in Task 0, applied in Task 3). Reuse points (`_prop`, `_ctx`, `resolve_url`, `export_document` arg names) say "grep for the existing one" — concrete instruction, not a placeholder.

**Type consistency:** `_pages`/`_slide`/`_placeholder`/`_layout_name`/`_pos_size` defined in Tasks 2/3/5 and reused verbatim after. Tool names match the spec table and the registry-test assertions. Slide args 1-based everywhere. Return dicts consistent (`{"slide":...}`).

## Notes on laziness (ponytail)

- Create/export reuse existing generic tools — no new doc-lifecycle plumbing.
- One `_placeholder(page, svc)` helper serves title/content/notes/read — not four finders.
- `impress_move_slide` is conditional: if UNO has no clean reorder, it's dropped, not faked.
- No theme/template/animation/table/chart/Draw scaffolding "for later" — those are separate increments in the spec's out-of-scope list.
