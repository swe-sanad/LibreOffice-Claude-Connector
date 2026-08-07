# Impress MVP — concrete design

Realizes **Priority 1** of [`PLAN-IMPRESS-BASE-DRAW.md`](PLAN-IMPRESS-BASE-DRAW.md)
(Impress: highest value, lowest risk). That doc argues *why* and *when*; this one
is the *what* and *how* for the first shippable increment. Base and Draw stay
deferred there.

**Date:** 2026-08-06 · **Branch:** `claude/focused-kepler-a4d52b`

## Goal

Give an agent the full presentation lifecycle in a **live** LibreOffice:
create a deck, structure it, fill title/bullets/notes/images/shapes, read it
back, and export a PDF — end to end, in one coherent `impress_*` family.

Not a `.pptx` generator. Driving a running office is the whole point: it buys us
the things a file-writer (python-pptx) structurally cannot do — real PDF/PNG
rendering, per-object animations, speaker notes, running the slideshow — which
land in later increments on the foundation this one sets.

## Competitive frame (why this supersedes what exists)

The repo's own [`COMPETITOR-STUDY.md`](COMPETITOR-STUDY.md) names the real rivals:
`quazardous/nelson-mcp` and `KeithCu/writeragent` cover all four apps and this is
"our largest user-facing gap." External survey adds two more:

| Competitor | Impress surface | Structural ceiling |
|---|---|---|
| `WaterPistolAI/libreoffice-mcp` | `insert_slide` + `add_shape` (2 tools, ~2 of 10 lifecycle stages) | trivial — no content, notes, or export |
| `GongRzhe/Office-PowerPoint-MCP-Server` (python-pptx), ~34 tools | strong: templates, themes, tables, charts, transitions | **cannot** render/export PDF or PNG, **no animations**, no notes tool, no live view — python-pptx limits |
| `nelson-mcp` / `writeragent` | all-four-app coverage | breadth over depth; not this lifecycle depth |

Our edge is the live office. This MVP already beats WaterPistolAI outright and
matches the python-pptx core loop **plus** the two things it can't do at all
(real PDF export, first-class speaker notes) and lays the foundation for the rest
(animations, PNG/slideshow) that only a live office enables.

## Locked decisions

- **Separate surfaces.** `impress_*` is its own family, not tools bolted onto
  Writer/Calc. Draw becomes a separate `draw_*` family in a later increment. Per
  `PLAN-IMPRESS-BASE-DRAW.md` §"What NOT to do".
- **Page addressing = 1-based slide index**, settled once here for every tool.
  A `slide` arg means "slide N as the user sees it" (1 = first). Rationale: agents
  and users both think "slide 3"; slide *names* ("Slide 1") are unstable and not
  user-meaningful. No name-addressing in the MVP.
- **Placeholders resolved by UNO service, never by index** —
  `com.sun.star.presentation.TitleTextShape` / `OutlineTextShape` /
  `SubtitleTextShape` / `NotesTextShape`. Robust to layout changes; the index of
  the title box moves when the layout changes, the service does not.
- **Reuse the generic doc-level tools.** Creating and exporting a presentation go
  through the *existing* `create_document` and `export_document` — one factory-URL
  row and one filter row each, no new plumbing. Not a violation of "separate
  surfaces": those tools are already cross-app.

## Phase 0 — live UNO probe (before finalizing signatures)

`PLAN-IMPRESS-BASE-DRAW.md` flags the placeholder model as *the* genuine unknown,
the same class of hazard as the Writer table-anchor and Calc force-text-marker
bugs. So we probe a real `simpress` doc first (a throwaway script in the
`scripts/spike_*.py` tradition) and confirm, against LO 25.2, exactly:

1. How `DrawPage.Layout` (int) maps to which placeholder shapes appear. Nail down
   the handful we use: title-only, title+content, title+subtitle, two-content, blank.
2. That `TitleTextShape` / `OutlineTextShape` are reliably found by
   `shape.supportsService(...)` after setting `Layout`, and that setting text on
   them lands in the placeholder (not a new floating box).
3. Bullet levels: whether the outline shape takes multiple paragraphs with a
   per-paragraph `NumberingLevel`, and how level 0..n render.
4. Notes: `slide.getNotesPage()` → the `NotesTextShape`, and that its string
   round-trips.
5. Image insert: `GraphicObjectShape` via the `com.sun.star.graphic.GraphicProvider`
   `Graphic` property (the non-deprecated path), positioned in 1/100 mm.
6. Whether `XDrawPageDuplicator.duplicate(page)` and any page-reorder API exist and
   behave. If reorder is fragile, `impress_move_slide` is dropped from the MVP (YAGNI).

The probe's findings get written back into this doc before the tool signatures are
frozen. Tools are not written against assumptions about the placeholder model.

### Phase 0 findings (LO 25.2.3.2, measured 2026-08-07 via `scripts/spike_impress.py`)

The probe overturned two assumptions — recorded here so the tools are built on
the real model:

- **Layout ints** (verified by `getCount` after `page.Layout = n`):
  `blank=20` (0 shapes), `title_only=19` (title only), `title_subtitle=0`
  (title + subtitle), `title_content=1` (title + outliner), `two_content=3`
  (title + 2 outliner). *The plan's earlier `title_only=20` was wrong.*
- **Placeholders resolve by INDEX, not by a stable text-shape service.** A fresh
  `simpress` page exposes placeholders as **empty presentation objects**
  (`IsEmptyPresentationObject=True`) at fixed indices: **title = index 0**
  (also reports `presentation.TitleTextShape`), **body = index 1** (reports
  `presentation.OutlinerShape`; index 2 for the 2nd content box), **subtitle =
  index 1** on the title-slide layout (reports only the generic
  `presentation.Shape`). `shape.setString(...)` writes into them and flips
  `IsEmptyPresentationObject` to False. So: resolve **title by the
  `TitleTextShape` service, body by the `OutlinerShape` service** (both reliably
  reported for content layouts), and fall back to index 1 for the subtitle.
  There is **no `OutlineTextShape`** service — it is `OutlinerShape`.
- **Notes:** `page.getNotesPage()` returns a page whose **index 0 is the slide
  thumbnail** (`com.sun.star.drawing.PageShape`, no text) and **index 1 is the
  notes text box** (`supportsService("com.sun.star.drawing.Text")` True, no
  presentation subtype). Resolve it as *the notes-page shape that supports
  `drawing.Text` and is not a `PageShape`*. Write round-trips.
- **Bullet levels:** per-paragraph `para.NumberingLevel` works, but the **default
  level reads back as `None`, not `0`** — coerce `None -> 0` on read.
- **Placeholder vs inserted shape:** an inserted drawing shape on a slide *also*
  reports `com.sun.star.presentation.Shape`, so that service cannot tell a layout
  placeholder from inserted content. The reliable discriminator is the
  **`IsPlaceholderDependent`** property: `True` for title/body/subtitle
  placeholders, `False` for anything added with `page.add()`. `read_slide`'s
  shape list and the subtitle fallback both key off it.
- **Images:** `com.sun.star.graphic.GraphicProvider.queryGraphic({"URL": ...})`
  is available; feed its result to `GraphicObjectShape.Graphic`.
- **Reorder:** `doc.duplicate(page)` exists; there is **no `moveByIndex`** and no
  other clean reorder API. Per the decision gate, **`impress_move_slide` is
  dropped** from the MVP (duplicate + delete + add cover the need). **Tool count
  is now 12, not 13.**

## Wiring changes (shared infra)

- `src/uno_bridge.py`: add `IMPRESS_DOC_SERVICE =
  "com.sun.star.presentation.PresentationDocument"` and `is_impress(doc)` next to
  the existing `is_calc`/`is_writer`. Slide/shape helpers for Impress live in a new
  section here after the Writer block.
- `mcp/libreoffice_mcp.py`:
  - `_FACTORY_URLS`: add `"impress": "private:factory/simpress"` and widen the
    `create_document` type enum.
  - `_FILTERS`: add the Impress PDF export row (`impress_pdf_Export`) and the
    native `impress8` filter so `export_document` handles `.pptx`/`.pdf`/`.odp`.
  - `_require_impress()` mirroring `_require_calc`/`_require_writer`.
  - `_BASIC_TOOLS`: add the everyday `impress_*` names (the ✅ rows below).
  - `_NO_UNDO`: add the read-only `impress_*` tools.

## The MVP tool set — 13 `impress_*` tools

All addressed by 1-based `slide` index. Read tools are `_NO_UNDO`. ✅ = advertised
in the default tier; the rest are reachable via `dispatch` / `LO_TOOLS=full`.

| Tool | Args (shape) | Behavior | Adv. |
|---|---|---|---|
| `impress_overview` | — | deck summary: count, per-slide layout name, title, text length, has-notes | ✅ |
| `impress_read_slide` | `slide` | full dump of one slide: title, outline text, shapes, notes | ✅ |
| `impress_add_slide` | `slide?`, `layout?` | insert a slide at index (default: end), apply an autolayout | ✅ |
| `impress_set_layout` | `slide`, `layout` | change an existing slide's autolayout | — |
| `impress_set_title` | `slide`, `text` | set the TitleTextShape string | ✅ |
| `impress_set_content` | `slide`, `bullets[]` | fill the OutlineTextShape; each bullet `{text, level?}` | ✅ |
| `impress_set_notes` | `slide`, `text` | set the NotesTextShape (competitor gap) | ✅ |
| `impress_insert_image` | `slide`, `path`, `x?,y?,w?,h?` | GraphicObjectShape from a local file, positioned (1/100 mm) | ✅ |
| `impress_insert_shape` | `slide`, `kind`, `x,y,w,h`, `text?` | auto shape (rect/ellipse/…) + optional text | ✅ |
| `impress_insert_text_box` | `slide`, `text`, `x,y,w,h` | free-floating TextShape | — |
| `impress_delete_slide` | `slide` | remove a slide | — |
| `impress_duplicate_slide` | `slide` | clone via `XDrawPageDuplicator` | — |

`impress_move_slide` was dropped after Phase 0 found no clean reorder API (see
findings above) — **12 tools, not 13**. Reordering is a next-increment concern.

`layout` is an enum of friendly names (`title`, `title_content`, `title_subtitle`,
`two_content`, `blank`, …) mapped to the `DrawPage.Layout` ints confirmed in Phase 0
— agents never pass raw ints. `kind` reuses the shape vocabulary already accepted by
`calc_add_shape` / `writer_insert_shape` where it maps cleanly.

Positions are 1/100 mm and assert with ±tolerance (LO round-trips through twips).

## Return / error conventions

Handlers return a plain dict (the JSON-RPC layer adds the human summary + JSON
blocks); raise on failure (classified by `_classify_error`). Same as every other
tool — no new envelope.

## Tests

- **Offline** — extend `tests/test_everyday_surface.py`: registry parity
  (`TOOLS`↔`TOOL_DEFS`) for the 13, tier membership for the ✅ rows, `_NO_UNDO`
  membership for the two read tools, `create_document` enum includes `impress`.
- **Live** — new `tests/integration/test_impress_uno.py`: load
  `private:factory/simpress`, build a 3-slide deck (title slide, a bulleted
  content slide with two indent levels, a slide with an image + notes), assert
  title/bullets/notes/shape round-trip via `impress_read_slide`, then
  `export_document` to a temp PDF and assert the file exists and is non-empty.
  Run under the isolated-profile harness (`scripts/run_integration.ps1`), no API key.

## Out of scope for the MVP → mostly delivered in increment 2

The next increment (see the CHANGELOG "Impress advanced + Draw surface" entry)
landed most of this:

- ✅ **Done:** slide transitions (`impress_set_transition`), PNG/SVG per-slide
  export (`impress_export_slides`), tables (`impress_insert_table`), charts
  (`impress_insert_chart`), slide background colour (`impress_set_background`),
  running the slideshow (`impress_slideshow`), and the separate `draw_*` surface
  (7 tools).
- ⛔ **Deferred — UNO not cooperative on LO 25.2 (probed + rendered):**
  - *Per-object animations* — the animation-node services
    (`ParallelTimeContainer`, `AnimateSet`, …) are not instantiable via the
    document factory, so the effect cannot be built; and a static slide render
    (the verification tool) cannot show a temporal effect. Not shipped.
  - *Native background / master-theme templating* — `page.Background` /
    `master.Background` fill does not apply (a red master background **rendered
    blue** when exported to PNG). `impress_set_background` instead lays a
    full-slide rectangle, which renders correctly; full master/theme templating
    is future work.
- **Still genuinely future:** embedding a *live* Calc chart, template galleries,
  auto-contrast text on dark backgrounds, and LibreOffice Base.

## Definition of done

Phase 0 probe findings recorded here · 12 tools registered and passing offline
registry/tier tests · live integration test builds a deck and exports a PDF green
· `CHANGELOG.md` updated · `PLAN-IMPRESS-BASE-DRAW.md` Impress section marked
"in progress → see this doc" · `docs/MCP-TOOLS.md` lists the new family.
