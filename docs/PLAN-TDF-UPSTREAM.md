# Plan — Rung 3: upstreaming native agent access into LibreOffice core

**Goal:** LibreOffice itself ships an opt-in *"Allow local AI agents to control
this office"* capability — no extension required — with native MCP as the end
state. This is a community process as much as a code change; the plan treats the
social steps as first-class tasks.

## Phase 0 — credibility package (before posting anything)

| # | Task | Output |
|---|------|--------|
| 0.1 | Ship the pipe-acceptor extension (PLAN-PIPE-ACCEPTOR.md) and get it listed on extensions.libreoffice.org with real users | the working prototype the RFC points at |
| 0.2 | Write the evidence note: the Kahatayn case study (a complete bilingual RTL NPO workbook built agent-first through 61 MCP tools), incl. what broke (RTL form-control serialization) — proof this exercises LibreOffice in ways users care about | `docs/CASE-STUDY.md` |
| 0.3 | Each contributor sends the **TDF license statement** to the dev list ("all my past & future contributions are under MPL-2.0/LGPLv3+") — required before any gerrit patch is merged | archived email |
| 0.4 | Land 1–2 **EasyHacks** (bugs.documentfoundation.org, keyword `easyHack`, `difficultyBeginner`) to learn the gerrit workflow and become a known name | merged trivial patches |

## Phase 1 — the RFC

| # | Task | Notes |
|---|------|-------|
| 1.1 | File the anchor ticket: Bugzilla **enhancement** "Opt-in local agent access (in-process UNO acceptor + Options toggle)" | gets a `tdf#` number everything references |
| 1.2 | Post the RFC to `libreoffice@lists.freedesktop.org`. Structure: problem (agents must launch LO with daemon flags), prior art (our extension, MCP ecosystem), proposal phase 1 (Options toggle + local pipe acceptor in core), proposal phase 2 (native MCP endpoint), **security model up front** (default OFF in core, local-only transport, macro-permission prompt, audit trail), what we commit to build | the security section will dominate the thread — write it best |
| 1.3 | Ask for an **ESC call** agenda slot (weekly Thursday call; agenda via the dev list) and present in 5 minutes | expect "make it default-off, put it behind Expert Configuration first" — accept happily |
| 1.4 | Loop in the **UX/design team** (ux-advise) for the Options page wording | small, buys goodwill |

## Phase 2 — the code (small patch series on gerrit)

| # | Patch | Where in core |
|---|-------|---------------|
| 2.1 | Config key `org.openoffice.Office.Common/Security/AgentAccess` (enable, pipe name) | `officecfg/registry/schema/` |
| 2.2 | Startup hook: when enabled, create the acceptor exactly like `--accept` does (reuse `desktop/source/app/officeacceptthread` / `binaryurp`) on the configured local pipe | `desktop/` — mostly wiring existing pieces |
| 2.3 | Options page checkbox + warning text | `cui/source/options/` |
| 2.4 | Unit/UI test: enable config → pipe resolvable → disable → gone | `desktop/qa/` |
| 2.5 | Release-notes entry + help page | `helpcontent2` |

Review etiquette: one logical change per patch, respond within days, expect 2–6
weeks of review latency per patch. A committer mentor from the ESC thread makes
this dramatically faster — ask for one explicitly.

## Phase 3 — native MCP endpoint (the big one)

- Scope: an in-process MCP server (stdio child or local socket), tools generated
  from UNO reflection, permission prompts per capability class (read / write /
  macro / file). C++ with a JSON-RPC lib already in core (`orcus`/`boost::json`).
- Realistic vehicles: **GSoC 2027 project** (TDF mentors; we co-mentor), or a
  **TDF tender** (the board funds targeted work), or incremental gerrit series if
  phase 2 landed smoothly and maintainers are engaged.
- Our fallback stays shipping: even if core says no, extension + auto-launch
  already deliver the UX; core adoption is leverage, not a dependency.

## Timeline & effort (honest)

| Milestone | Calendar |
|---|---|
| Phase 0 complete | 2–3 weeks (mostly waiting on extension listing review) |
| RFC posted + ESC discussed | 1–2 weeks after that |
| Phase 2 series merged | 2–4 months of intermittent effort (review latency dominates) |
| Native MCP | 6–18 months, driven by GSoC/tender cycles |

**Kill criteria:** if the ESC rejects even the opt-in acceptor, stop pushing core
and double down on the extension channel — same UX, different install story.
