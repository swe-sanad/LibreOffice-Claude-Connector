# Competitor study — Nelson MCP and WriterAgent, read first-hand

**Date:** 2026-07-26 · **Method:** both repositories cloned and read at
`quazardous/nelson-mcp` and `KeithCu/writeragent` (shallow clones, source read
only, nothing executed). Supersedes the second-hand triage in
[`UPSTREAM-PARITY.md`](UPSTREAM-PARITY.md), which was assembled from a Copilot
enumeration of tool names and never checked against the code.

Three lenses, as commissioned: verify the triage, look at them through the
everyday-user lens of v0.10.0, and re-examine the strategic position.

---

## 0. Scale — the part the old triage understated

| | this repo | Nelson MCP | WriterAgent |
|---|---|---|---|
| Python LOC | ~7k | **45k** | **80k** |
| tracked files | ~90 | 358 | 1,531 |
| last commit | 2026-07-26 | **2026-07-25** | **2026-07-25** |
| apps | Calc, Writer | Writer, Calc, Draw, Impress | Writer, Calc, Draw, Impress |
| transport | stdio | HTTP-in-extension | HTTP-in-extension + stdio |

Both are actively developed, an order of magnitude larger, and cover two
applications we do not. `UPSTREAM-PARITY.md` closes with "remaining to fully
supersede all five" — on this evidence **supersession by breadth is not a
realistic goal**, and pursuing it would spend the project's advantage rather
than build on it. Recommendation in §4.

---

## 1. The finding that matters most: Nelson evaluated our v0.10.0 design and rejected it

`nelson-mcp/docs/analysis/tool-broker-decision.md`, closed **2026-07-25 — the day
before we shipped tiering**. They call it "progressive tool disclosure"; it is
structurally the same thing as our everyday tier plus `dispatch`.

Their measured numbers, against a live Writer session:

| what the client receives | tools | tokens |
|---|---|---|
| their default, after document-type filtering | 94 | 17,107 |
| a `core` opening tier | 17 | 3,540 (21 %) |
| their existing `minimal` preset | 8 | 1,821 (11 %) |

Ours, for comparison: 174 → 32 advertised, 84 KB → 15 KB of schema (~23k → ~4k
tokens). **The saving is real and both projects agree on its size.** The
disagreement is about the cost, and their three arguments deserve a straight
answer rather than a defence:

1. **"It reintroduces #24 by design."** Their issue #24 was a user reporting that
   Nelson "simply *lacks* spreadsheet support" because a client had cached a tool
   list and never saw Calc tools appear. Hiding tools behind a request step makes
   that the intended behaviour. **This applies to us.** A model that sees 32 tools
   and never thinks to call `dispatch` will conclude we cannot make a pivot table.
2. **"The premise is contradicted by the evidence."** Their issue #2 was a model
   picking the wrong tool *while the right one was visible and unambiguously
   named*. If a model fails at picking from a list, asking it to first infer a
   capability might exist, then request it, then call it, is strictly harder.
   **This is a fair hit.** Our 82 % context saving is measured; the claim that it
   *improves tool-selection accuracy* is not — I asserted it, and it is exactly
   the kind of claim their document calls "an API change justified by taste".
3. **"MCP offers no standard affordance for requesting more tools."** True, and
   `dispatch` is a custom tool competing for attention with the ones it gates.

### What they built instead — and what we are missing

Rather than a broker, Nelson does three things we do not:

- **Document-type filtering.** `tool.doc_types` (`tool_registry.py:180`) removes
  Writer tools from the list when a Calc document is in front, and calling one
  anyway returns a structured error with `"hint": "Open a %s document first."`
  This is strictly better than tiering for our biggest redundancy: we advertise
  62 `writer_*` tools to someone editing a spreadsheet.
- **`instructions` in the `initialize` response** (`protocol.py:342`), spent on
  decisions, not trivia. It explicitly says: *"THE TOOL LIST DEPENDS on the type
  of the active document, so a tool you need may be absent simply because the
  wrong document is in front."* That single sentence is what makes an incomplete
  list safe. **We send no `instructions` at all.**
- **`capabilities.tools.listChanged: true`** plus real
  `notifications/tools/list_changed` sends (`mcp/__init__.py:243`) when the
  active document changes.

**Honest assessment of v0.10.0:** the tiering is defensible and the context
saving is real, but we shipped the mechanism without either safety net that makes
a reduced list safe — no `instructions` explaining the omission, no
`listChanged`. Nelson's critique lands. Remediation in §4; none of it requires
reversing the release.

---

## 2. Undo — we are behind Nelson and level with WriterAgent

v0.10.0 established that `setDataArray`/`setFormulaArray` register an undo entry
that does not restore prior contents. Checked against both:

| | `setDataArray` uses | `enterUndoContext` uses | verdict |
|---|---|---|---|
| this repo | via `uno_bridge.write_range_grid` | central, 1 site | hole in `calc_write_range` |
| **Nelson** | **0** | central, 1 site (`tool_registry.py:281`) | **no hole** |
| WriterAgent | 11 (`calc/manipulator.py`, `calc/python/function.py`) | 21 sites | likely the same hole |

Nelson's wrapper is near-identical to the one I wrote today — `if mutates and
ctx.doc is not None`, `enterUndoContext("Nelson: %s [%s]")`, leave in a finally.
Independent convergence on the design is reassuring. Two differences worth
copying:

- They carry an explicit **`mutates` flag declared per tool**. We use `_NO_UNDO`,
  a denylist — a new read-only tool that nobody adds to the set silently gets a
  pointless undo context, and the failure is invisible. A declared property is
  the more robust shape.
- **Nelson uses `setDataArray` nowhere**, so its undo grouping is sound where
  ours is not. This is a genuine, verifiable advantage they hold over us on Calc.

WriterAgent writes ranges with `setDataArray` (their own comment: *"to avoid O(N)
individual cell writes"* — the same tradeoff I reasoned through) while wrapping
operations in a `WriterCompoundUndo` class whose name and call sites are
Writer-side. Their Calc bulk writes are therefore probably as un-revertable as
ours. Not proven — it would need running their code — but the caveat added to
`UPSTREAM-PARITY.md` stands.

---

## 3. The strategic fork was based on a false premise

`UPSTREAM-PARITY.md` says WriterAgent's data-science layer is unreachable for us
because it "needs NumPy/pandas/SciPy/SymPy/DuckDB/embeddings — none installable
in LibreOffice's bundled Python without `pip`", and frames matching it as
requiring "a deliberate decision to bundle third-party deps".

**That is not how WriterAgent does it.** Everything it vendors into the extension
is five small pure-Python packages:

```
snowballstemmer · json-repair · latex2mathml · websockets · defusedxml
```

No NumPy. No pandas. The heavy work lives in `compute_service/` — a **standalone
HTTP service in its own Docker image** (`python:3.12-slim`, `pip install` at build
time), which the office POSTs to at `/v1/execute` with a bearer token. It is a
sidecar, not a bundled dependency.

The consequences for our roadmap are direct:

- **Stdlib-only inside LibreOffice's Python is not the constraint we thought it
  was.** It never prevented a data-science layer; it only prevents one *in
  process*. The architecture that unlocks it — an optional local HTTP sidecar —
  costs us nothing in the default install and does not touch the locked decision.
- The real question is therefore not "do we abandon stdlib-only?" (we should not)
  but **"do we want to own an optional compute sidecar?"** — a much smaller,
  reversible decision, and one a user who never installs it never pays for.
- It also removes the strategic-fork framing from the parity doc, which
  overstated the tradeoff and made the DS layer look like an all-or-nothing bet.

---

## 4. Recommendations

**Adopt (cheap, clearly right):**

1. **`instructions` in `initialize`.** Highest value per line of code in this
   list. Must state that the advertised list is partial and that `dispatch` with
   `tool='list'` is the authoritative catalog — this is the direct answer to
   Nelson's critique #1. Nelson's text is a good model: spend it on decisions.
2. **Document-type filtering.** Do not advertise 62 `writer_*` tools to someone in
   Calc. Larger real saving than tiering, and no discoverability cost — the tools
   are absent only while they are inapplicable.
3. **`listChanged: true` + notifications** when the active document changes.
   Prerequisite for (2) being safe.
4. **Replace `_NO_UNDO` with a declared `mutates` property per tool** — denylists
   drift silently.
5. **Structured errors** `{code, message, hint}`. Already on the roadmap; the
   first-hand read confirms the payoff — "Open a Calc document first" is a
   recoverable error, `RuntimeError: The active document is not a Calc
   spreadsheet` is not.

**Measure before defending:** Nelson's standard — a small model, fixed realistic
requests, first-tool-call accuracy with and without tiering — is the right bar,
and we have not cleared it. Until then the honest claim for v0.10.0 is "82 % less
context", not "better tool selection".

**Re-frame, do not chase:**

- Drop "supersede all five" as a goal. Against 45k and 80k LOC of actively
  developed code covering four applications, breadth parity is not winnable and
  is the wrong fight.
- What is actually defensible: **zero-dependency install**, **any stdio MCP
  client** (they are both HTTP-in-extension first), **the everyday-user surface**,
  and being the only one of the three that is Claude-native end to end (the `.oxt`
  and the MCP server are one product).
- **HTTP transport** stays the top roadmap item — it is the one place where their
  architecture is simply more capable, and it unblocks remote clients.
- **The compute sidecar** is now a live option rather than a forbidden one (§3).

---

## Appendix — how to re-run this

```bash
git clone --depth 1 https://github.com/quazardous/nelson-mcp
git clone --depth 1 https://github.com/KeithCu/writeragent
```

Read, do not execute. The load-bearing files:

| what | where |
|---|---|
| the anti-tiering argument | `nelson-mcp/docs/analysis/tool-broker-decision.md` |
| doc-type filtering | `nelson-mcp/plugin/framework/tool_registry.py:180,229` |
| undo wrapper + `mutates` | `nelson-mcp/plugin/framework/tool_registry.py:279` |
| `instructions` + `listChanged` | `nelson-mcp/plugin/modules/mcp/protocol.py:329` |
| the DS sidecar | `writeragent/compute_service/README.md`, `Dockerfile` |
| what they actually vendor | `*/requirements-vendor.txt` |
| their bulk writes | `writeragent/plugin/calc/manipulator.py:652` |
