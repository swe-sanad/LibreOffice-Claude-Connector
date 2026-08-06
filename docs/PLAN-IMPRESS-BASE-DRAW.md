# Deferred apps — Impress, Draw, Base

> **Impress: IN PROGRESS / landed as an MVP** — the 12-tool `impress_*` family is
> built; see [`PLAN-IMPRESS-MVP.md`](PLAN-IMPRESS-MVP.md) for the concrete design,
> the live Phase-0 findings, and what is deferred to the next increment
> (transitions, animations, slideshow, PNG export, tables, charts). **Draw and
> Base remain deferred as below.**

**Status:** deliberately not started. Each needs its own working session; none of
them is a "add a few tools to the Writer/Calc surface" job. Recorded here so the
decision is not re-argued from intuition, and so whoever picks it up starts from
the actual shape of the problem.

Current surface covers **Writer and Calc only**. Both sibling projects
(`quazardous/nelson-mcp`, `KeithCu/writeragent`) cover all four applications —
see [`COMPETITOR-STUDY.md`](COMPETITOR-STUDY.md). This is our largest
user-facing gap against them.

## Priority when we do pick this up

**1. Impress — highest user value, lowest risk.**

Claude is genuinely good at producing presentation content (structure, speaker
notes, per-slide narrative), which makes this the deferred app most likely to
delight a student. The API is not exotic: `XDrawPagesSupplier` for the deck,
`XDrawPage` per slide, and the same shape/text APIs already used by
`calc_add_shape` and `writer_insert_shape`. Layouts come from
`presentation.Layout` on the page; speaker notes hang off `getNotesPage()`.

Rough scope: create/delete/reorder slides, set layout, fill title and outline
placeholders, speaker notes, per-slide read-back, export to PDF. That is one
coherent family (`impress_*`), maybe 15–20 tools.

The genuine unknowns are the placeholder model (getting text into the *right*
box rather than a floating shape) and master slides / templates. Both need
live probing before designing the tool signatures — the same discipline that
caught the table-anchor and force-text-marker bugs in Writer/Calc.

**2. Base — highest effort, narrowest audience.**

A whole new domain: `com.sun.star.sdb` connections, queries, forms and reports.
It needs a connection model this server does not have (the other tools all act
on "the open document"; a database is a connection, sometimes to an external
engine). Also the largest surface where getting it wrong destroys user data.

Worth doing only if there is real demand. `waterpistolai` has it and is
otherwise superseded, which is the only reason it is on the list at all.

**3. Draw — smallest gap.**

Shares the drawing model with Impress; once Impress lands, most of Draw is the
same APIs against a different document type. Little standalone value — do it as
a follow-on to Impress, not before it.

## What NOT to do

Do not bolt slide tools onto the Writer/Calc families. The existing tools assume
`_require_writer()` / `_require_calc()` and a single active document; Impress
needs its own `_require_impress()` and a page-addressing convention
(index? name? both?) settled once, up front, rather than per tool.

## Prerequisites already in place

- The tier mechanism means adding 20 `impress_*` tools costs the everyday user
  nothing — they stay unadvertised unless a presentation is open. (Document-type
  filtering, borrowed from Nelson, would make that automatic; see the
  recommendations in `COMPETITOR-STUDY.md` §4.)
- Undo grouping, structured errors, the call timeout and `checkpoint_document`
  all apply to any new family for free.
