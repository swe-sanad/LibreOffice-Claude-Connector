# LibreOffice ↔ Claude Connector — Fact-Checked Research Report

*Compiled 2026-07-09. Every load-bearing claim below was independently verified with a
3-vote adversarial fact-check (24 of 25 claims confirmed unanimously; 1 overreach refuted).
Sources are listed at the end of each section and consolidated at the bottom.*

---

## 0. Executive summary

**Goal.** Bring Claude AI into LibreOffice **Calc** and **Writer** on Windows 11.

**Recommended architecture (validated).** A **Python extension (`.oxt`)** that runs
**in-process** inside LibreOffice, drives documents through the **UNO API**, and calls the
**Anthropic Messages API directly over HTTPS using only the Python standard library**
(`urllib.request` + `json` + `ssl`) — no `requests`, no SDK, no compiled wheels. AI actions
are triggered from a **menu / toolbar / keyboard shortcut** (and later a sidebar panel),
never as Calc `=CLAUDE()` formula functions.

**Why this is low-risk.** It is already a proven pattern. **`localwriter`** (Python/UNO,
MPL-2.0, ~176★, last updated 2026-07-07, endorsed on the LibreOffice blog) does inline
generative editing in Writer exactly this way, and **`libre-ai`** already targets Claude.
Everything the connector needs — batch cell I/O, selection replacement, declarative menus,
HTTPS from bundled Python — is confirmed against primary LibreOffice/Anthropic sources.

**The 3 corrections the fact-check forced:**
1. Bundled Python is **3.9.22 in LO 24.8** and **3.11.13 in LO 25.8** (not 3.10). Target
   3.9 stdlib; do **not** assume 3.13 (that claim was refuted).
2. The Windows "HTTPS fails from bundled Python" hazard was **fixed in 2017** — works today.
3. Current Claude model IDs are **dateless pinned snapshots** (`claude-opus-4-8`,
   `claude-sonnet-5`, `claude-haiku-4-5`), not `YYYYMMDD` strings. Pinning is safe; still
   make the model user-configurable.

---

## 1. Extension foundations (architecture, language, packaging)

### 1.1 UNO — the component model
UNO (Universal Network Objects) is LibreOffice's language-neutral component system
(interfaces `X...`, services, structs). Three usage modes matter:

- **In-process** — code runs *inside* `soffice`. This is what a shipped `.oxt` uses.
- **Out-of-process** — an external program drives office over a **socket** or **named pipe**
  using the **URP** protocol. Dev/testing only.
- **Headless engine** — office as a library.

Three objects anchor everything: the **component context** (`XComponentContext`), the
**service manager** (`XMultiComponentFactory`), and the **Desktop**
(`com.sun.star.frame.Desktop`). Inside a macro/extension you get them for free via the
injected `XSCRIPTCONTEXT` (→ `.getDocument()`, `.getDesktop()`, `.getComponentContext()`)
or `uno.getComponentContext()`. **Ship in-process; never open a socket in the product.**

### 1.2 Language choice → Python (recommended)

| Language | HTTPS story | Doc access | Verdict |
|---|---|---|---|
| **Basic** | Weak (no native HTTPS/JSON) | Full | Prototype/launcher only |
| **Python (PyUNO)** | **Strong — stdlib `ssl`+`urllib`+`json`, zero deps** | Full, in-process | ✅ **Recommended** |
| Java | Strong | Full | Heavy; drags a JRE dependency |
| C++/native | Strong (you own it) | Full | Overkill; per-arch builds |

Python wins: no compilation, direct document access via `XSCRIPTCONTEXT`, HTTPS with the
standard library alone, best community precedent (localwriter is Python).

### 1.3 Bundled Python on Windows (VERIFIED)
- Interpreter: `C:\Program Files\LibreOffice\program\python.exe`; core lib under
  `program\python-core-<version>\`.
- **Version: LO 24.8 → Python 3.9.22; LO 25.8 → 3.11.13.** (25.2 not pinned down — check
  with `python.exe --version` on the target; **target 3.9 stdlib** for safety.)
- `requests` is **not** bundled and pip isn't shipped (and `Program Files` isn't writable) —
  **so use the stdlib.** `ssl`/`http.client`/`urllib.request`/`json` are present and make
  HTTPS calls fine. The old Windows "wrong OpenSSL DLL on PATH" bug (tdf#109241) is
  **RESOLVED FIXED since 2017**.
- If you ever need third-party pure-Python deps, **vendor them inside the `.oxt`** and add a
  `pythonpath/` folder (auto-added to `sys.path` by the Python script provider). Avoid
  compiled wheels (they must match the bundled interpreter's exact ABI).

### 1.4 `.oxt` package format (VERIFIED)
An `.oxt` is a **ZIP** (zip the *contents* so `META-INF/` and `description.xml` sit at the
root). Minimum viable layout for this connector:

```
claude-connector.oxt  (zip)
├── META-INF/manifest.xml     # lists every file + its UNO media-type
├── description.xml           # identity, version, min-office-version, license, icon
├── Addons.xcu                # menu + toolbar items  (configuration-data)
├── ProtocolHandler.xcu       # maps custom command URLs → the dispatch component
├── Accelerators.xcu          # optional keyboard shortcuts (localwriter uses Ctrl+Q/Ctrl+E)
├── Sidebar.xcu               # optional chat panel (later)
├── python/connector.py       # PyUNO component (XDispatchProvider/XDispatch)
├── pythonpath/               # optional vendored pure-python deps
└── icons/ , description/      # icons + long description text
```

Key `manifest.xml` media-types: `application/vnd.sun.star.uno-component;type=Python`
(the `.py` component) and `application/vnd.sun.star.configuration-data` (each `.xcu`).
`description.xml` needs a unique reverse-DNS `<identifier>` (that's what `unopkg remove`
takes) and an `OpenOffice.org-minimal-version` gate.

### 1.5 UI wiring (VERIFIED)
- **Menus/toolbars are declarative** in `Addons.xcu` (`AddonUI` → `OfficeMenuBar` /
  `OfficeToolBar` / `Images`), with a **`Context`** property to scope items to
  `com.sun.star.sheet.SpreadsheetDocument` (Calc) and/or `com.sun.star.text.TextDocument`
  (Writer).
- A menu item carries a **custom command URL** like `com.example.claudeconnector:AskClaude`.
- **`ProtocolHandler.xcu`** binds that URL prefix to your component, which implements
  **`XDispatchProvider` + `XDispatch`** (`queryDispatch` returns self; `dispatch()` is where
  the connector logic runs). *(Refinement from the fact-check: the handler implements
  `XDispatchProvider`; `XDispatch` is the object it returns.)*
- **Context-menu** entries are **not** part of classic `Addons.xcu` — they need a runtime
  `XContextMenuInterceptor`. Sidebar decks/panels are declared in `Sidebar.xcu` + a
  `XUIElementFactory` component.

### 1.6 Deployment (VERIFIED)
- `unopkg add <file.oxt>` / `unopkg remove <identifier>` (add `--shared` for all-users; LO
  must be closed for `--shared`; `-f` to force-reinstall, `--suppress-license` for scripted
  installs). Binary: `C:\Program Files\LibreOffice\program\unopkg.com`.
- GUI: **Tools ▸ Extension Manager ▸ Add** (or double-click the `.oxt`).
- Per-user install path: `%APPDATA%\LibreOffice\4\user\uno_packages\cache\`.
- **Dev gotcha:** LibreOffice **caches Python modules — restart LO after code changes.**

*Sources: api.libreoffice.org SDK/IDL; wiki.documentfoundation.org SDKGuide/Add-ons,
Config-extension-writing PDF; bugs.documentfoundation.org tdf#109241;
mail-archive.com LibreOffice 24.8 `configure.ac` commit; niocs.github.io LOBook.*

---

## 2. Calc integration (VERIFIED)

**Object path:** `doc = XSCRIPTCONTEXT.getDocument()` → `doc.getSheets()` →
`sheet.getCellByPosition(col,row)` (0-based) / `sheet.getCellRangeByName("A1:C100")`.

**Read the user's selection:** `doc.getCurrentSelection()` (≡
`doc.getCurrentController().getSelection()`). Dispatch on `supportsService(...)`, testing
**`com.sun.star.sheet.SheetCell` → `SheetCellRange` → `SheetCellRanges`** in that order;
always keep an `else` (a shape/chart can be selected).

**Batch I/O — the workhorse (`com.sun.star.sheet.XCellRangeData`, verified real):**
```python
data = rng.getDataArray()          # tuple-of-tuples: numbers→float, text→str, empty→""
# ...serialize → Claude → reshape...
rng.setDataArray(results)          # ONE bridge call
```
**Hard constraints (verified against the IDL):** `setDataArray` **raises RuntimeException
unless the 2-D array dimensions EXACTLY match the range**, and each cell must be `str` or a
number — **coerce `None`→`""` and JSON `null`→`""` before writing** (the #1 AI-output bug).
Use `XCellRangeFormula`.`setFormulaArray` to write actual formulas in bulk.

**Do NOT use a Calc Add-In (`=CLAUDE(...)`).** Verified: an Add-In requires typed **IDL
interfaces** (`XAddIn` + `XServiceName`) registered against
`com.sun.star.sheet.AddIn` — much heavier than a macro — *and* the formula engine recomputes
functions in bulk (on load, on any dependency change), which for a slow, paid, networked,
non-deterministic AI call is exactly wrong. **Trigger via menu/toolbar/shortcut instead.**

*Sources: api.libreoffice.org XCellRangeData, AddIn service; help.libreoffice.org
read/write ranges; ask.libreoffice.org selection thread.*

---

## 3. Writer integration (VERIFIED)

**Object path:** `model = XSCRIPTCONTEXT.getDocument()` (`XTextDocument`) →
`oText = model.getText()`. The **model** holds content; the **view/controller** holds the
user's caret/selection.

**Read the selection:** `model.getCurrentSelection()` returns a
`com.sun.star.text.TextRanges` collection (`XIndexAccess`, index 0 = first range) →
`.getByIndex(0).getString()`. Or the **view cursor**:
`model.getCurrentController().getViewCursor()` (verified: it's *the same instance* the user
sees); `.isCollapsed()` is `True` when there's only a caret.

**The core edit primitive — `XSimpleText.insertString(xRange, aString, bAbsorb)`
(verbatim from the IDL):**
- `bAbsorb=True` → **replaces** the range content ("rewrite this selection").
- `bAbsorb=False` → **inserts** at the range start, leaving content ("insert at caret").

```python
def apply_ai_text(model, new_text):
    vc = model.getCurrentController().getViewCursor()
    if vc.isCollapsed():                                  # no selection → insert
        vc.getText().insertString(vc.getStart(), new_text, False)
    else:                                                 # selection → replace
        vc.getText().insertString(vc, new_text, True)
```

**Multi-paragraph output:** split on `\n`, `insertString` each chunk, and put a real
paragraph break between them with `insertControlCharacter(cursor, PARAGRAPH_BREAK, False)`
(`com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK = 0`). Formatting is set as properties
on a cursor (`CharWeight` NORMAL=100/BOLD=150, `CharPosture`, `ParaStyleName`).

**Bulk find/replace (verified):** `model.createReplaceDescriptor()` + `model.replaceAll(desc)`
(interface `com.sun.star.util.XReplaceable`, subclass of `XSearchable`; the *same* interface
works on Calc ranges too).

**Gotchas:** wrap edits in one undo step (`XUndoManager.enterUndoContext`), `lockControllers()`
during big edits, and note `getText().getString()` is body-only and uses `\r` between
paragraphs. Preserving styles when replacing a selection is genuinely fiddly (WriterAgent's
"surgical replacement") — budget for it.

*Sources: api.libreoffice.org XSimpleText, XTextViewCursorSupplier, XReplaceable, ControlCharacter,
FontWeight; wiki.documentfoundation.org SDKGuide Search_and_Replace; LibreOffice/core sw python tests.*

---

## 4. Claude Messages API from bundled Python (VERIFIED)

**Endpoint:** `POST https://api.anthropic.com/v1/messages`
**Headers:** `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`.
**Body:** `{model, max_tokens, messages:[{role,content}], system?, temperature?, stream?}`.
The **system prompt is a top-level `system` field**, not a `role:"system"` message (differs
from OpenAI). **Response `content` is an ARRAY of blocks** — concatenate every block whose
`type=="text"`; check `stop_reason` (`max_tokens` ⇒ truncated) and `usage` for cost.

**Current model IDs (VERIFIED against platform.claude.com + anthropics/skills):** dateless
pinned snapshots — `claude-fable-5` (most capable), `claude-opus-4-8` (heavy reasoning),
`claude-sonnet-5` (default balance), `claude-haiku-4-5` (fast/cheap; snapshot
`claude-haiku-4-5-20251001`). Pinning a bare ID is safe (Anthropic never mutates weights of
an existing ID). **Still store the model in user-editable config** and optionally populate a
picker from `GET /v1/models`.

**Recommended defaults:** Haiku for quick inline edits, Sonnet as the general default, Opus
for heavy analysis only.

**Zero-dependency reference client** (verified pattern; see `reference/claude_client.py`
when implemented): `urllib.request.Request(...)` + `ssl.create_default_context()` +
`json`, with `timeout=`, retry on 429/500/502/503/529 honoring `retry-after`, and
`HTTPError` handling for 401/400. **Run the call on a `threading.Thread`** — a blocking
`urlopen` on the UI thread **freezes LibreOffice** — and marshal the result back to the main
thread *before* touching the document (UNO mutations from a worker thread are unsafe).

**Windows TLS:** `ssl.create_default_context()` uses the OS trust store and normally "just
works"; bundle `certifi`'s `cacert.pem` as a `cafile=` fallback. **Never disable verification.**

**Streaming (`"stream": true`, SSE):** improves perceived latency for long Writer
generations; **ship non-streaming first**, add streaming later.

**Key storage:** best = **OS keychain / Windows DPAPI** (via `ctypes`, zero-dep) — the model
`libre-ai` uses. Env var `ANTHROPIC_API_KEY` as a dev fallback. **Never hardcode** (an `.oxt`
is a zip; `LibreOfficeAICopilot` hardcoded its key — the anti-pattern to avoid).

**Token/size limits:** send the *selection/relevant range*, not whole huge docs; pre-check
with `POST /v1/messages/count_tokens`; track `usage`.

*Sources: platform.claude.com/docs Messages API + models overview + model-ids-and-versions;
github.com/anthropics/skills claude-api/shared/models.md.*

---

## 5. Prior art (proves viability)

| Project | Lang | Apps | LLM | Takeaway |
|---|---|---|---|---|
| **localwriter** ✅verified | Python/UNO | Writer | local/OpenAI-compat | **The `.oxt` template to copy.** `description.xml`+`META-INF`+`Addons.xcu`+`Accelerators.xcu`+`pythonpath/`. Hotkeys Ctrl+Q/Ctrl+E. |
| **libre-ai** | (C++/Qt6) | Writer/Calc/Impress | **Claude** + others | Proves Claude works; **keys in OS keychain (DPAPI)** — copy this. |
| CalcuLLM | likely Python | Calc | **Claude** | Sidebar for formulas/analysis (README wasn't fetchable — inspect directly). |
| WriterAgent | Python 3.11+ | all | OpenAI-compat | Best lessons: worker-pool threading, JSON-repair, surgical style-preserving replace. |
| "AI assistant with ChatGPT" (LibOCon 2023) | Python | Writer | OpenAI | Uses exactly `uno`+`urllib`+`json` — the stdlib pattern we recommend. |
| smonux/libreoffice-llm-plugin | Python (single macro) | Writer | OpenAI-compat | Simplest possible reference. |

Repos: github.com/balisujohn/localwriter · github.com/aronweiler/libre-ai (a.k.a. devilish84/LibreAI) ·
github.com/mostlyblocks/CalcuLLM · github.com/KeithCu/writeragent ·
extensions.libreoffice.org/en/extensions/show/41988 · github.com/smonux/libreoffice-llm-plugin

*(Inverse architecture worth knowing: several MCP servers let an **external** Claude drive
LibreOffice — github.com/patrup/mcp-libre etc. Opposite of embedding Claude in LO, but a
fallback if in-process threading proves painful.)*

---

## 6. Consolidated risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Blocking HTTP freezes the UI | High | Worker thread + main-thread result marshalling (every serious prior-art project does this) |
| `setDataArray` dimension/`None` errors | Med | Validate shape; coerce `None`/`null`→`""`; ints→floats before write |
| Bundled Python version drift (3.9→3.11) | Med | Target 3.9 stdlib; no compiled wheels; `python.exe --version` check in setup |
| Style loss on Writer replace | Med | Cursor-property preservation / surgical replace; undo context |
| API key leakage | High | DPAPI-encrypted blob; never hardcode; never disable TLS verify |
| Model ID churn | Low | Dateless pinned IDs are stable; keep model in config |
| LO caches Python modules | Low (dev) | Restart LO after each change; use APSO during dev |

---

## 7. Sources (primary)
- LibreOffice IDL/SDK: api.libreoffice.org (XCellRangeData, XSimpleText, XReplaceable,
  XTextViewCursorSupplier, AddIn, ControlCharacter, FontWeight); wiki.documentfoundation.org
  (SDKGuide/Add-ons, Search_and_Replace, Config-extension-writing PDF)
- Bundled Python: mail-archive.com LibreOffice 24.8 `configure.ac` commit (Python 3.9.22);
  bugs.documentfoundation.org tdf#109241 (HTTPS DLL fix)
- Claude API: platform.claude.com/docs (Messages API, models overview, model-ids-and-versions);
  github.com/anthropics/skills claude-api/shared/models.md
- Prior art: github.com/balisujohn/localwriter; github.com/aronweiler/libre-ai; and the table above
