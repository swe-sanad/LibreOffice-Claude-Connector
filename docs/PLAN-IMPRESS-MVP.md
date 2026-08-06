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
| `impress_move_slide` | `slide`, `to` | reorder (dropped if Phase 0 finds no clean API) | — |

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

## Out of scope (next increments — flagged, not forgotten)

Slide transitions · per-object animations · running the slideshow · PNG/SVG
per-slide export · tables · charts (incl. embedding a live Calc chart) ·
master-slide / theme / template editing · the separate `draw_*` surface · Base.

## Definition of done

Phase 0 probe findings recorded here · 13 tools registered and passing offline
registry/tier tests · live integration test builds a deck and exports a PDF green
· `CHANGELOG.md` updated · `PLAN-IMPRESS-BASE-DRAW.md` Impress section marked
"in progress → see this doc" · `docs/MCP-TOOLS.md` lists the new family.
