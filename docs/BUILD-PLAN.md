# LibreOffice ↔ Claude Connector — Build Plan

*Derived from the fact-checked findings in [RESEARCH.md](RESEARCH.md). Phased so each step
produces something observable before the next adds complexity.*

## Status (2026-07-10)

| Phase | State | Evidence |
|---|---|---|
| 0 — Environment & spike | ✅ done | LO 25.2.3.2 / bundled Python 3.10.17; live 401 from api.anthropic.com proves TLS from bundled Python |
| 1 — Core Claude client | ✅ done | `src/claude_client.py`; 14 unit tests |
| 2 — Calc rewrite-selection | ✅ done | `calc_actions.py` + `uno_bridge.py`; UNO integration test passes on real LO |
| 3 — Writer rewrite/generate | ✅ done | `writer_actions.py` + Writer UNO helpers; integration test passes |
| 4 — Package as `.oxt` | ✅ done | `ext/` + `connector.py` + `scripts/build_oxt.py`; installs & dispatch RESOLVES in real LO |
| 5 — Config & DPAPI key | ✅ done | `config.py` + `keystore.py`; 62 offline tests incl. real DPAPI round-trip |
| 6 — Feature depth & polish | ⏳ next | sidebar panel, streaming, more commands, style-preserving replace |
| 7 — Distribution | ⏳ next | publish to extensions.libreoffice.org, update feed |

**Verified so far:** 62 offline unit tests pass on bundled Python 3.10.17; Calc + Writer
document edits pass against real headless LibreOffice; the built `.oxt` installs and its
ProtocolHandler resolves in LibreOffice 25.2.3.2. Not yet exercised: a live end-to-end
Claude call from inside LibreOffice (needs an `ANTHROPIC_API_KEY`) and the interactive
dialogs (manual).

## Target architecture (one picture)

```
LibreOffice (Calc / Writer)  ── in-process ──►  Python .oxt extension
      ▲                                              │
      │ UNO (getCurrentSelection,                    │  worker thread
      │      getDataArray/setDataArray,              ▼
      │      insertString bAbsorb)          stdlib HTTPS (urllib+json+ssl)
      │                                              │
      └────────── write results back ◄───────────────┘
                  (on the MAIN thread)          POST api.anthropic.com/v1/messages
                                                x-api-key • anthropic-version: 2023-06-01
```

- **Trigger:** menu "Claude" + toolbar + shortcut → command URL → `ProtocolHandler` →
  `XDispatch.dispatch()`. (Not `=CLAUDE()` formulas.)
- **HTTP:** stdlib only, on a worker thread; results marshalled back to the main thread.
- **Config:** model + options in the user profile; **API key via Windows DPAPI**.

## Phases

### Phase 0 — Environment & spike (½ day)
- Install LibreOffice (note the version); confirm bundled Python:
  `& "C:\Program Files\LibreOffice\program\python.exe" --version`.
- Install **APSO** (dev-time Python organizer/console) via Extension Manager.
- Clone **localwriter** and **libre-ai** as reference.
- **Spike:** run a stdlib HTTPS smoke test with the *bundled* `python.exe` hitting
  `api.anthropic.com/v1/messages` (a 1-line Haiku call). Proves TLS + connectivity before any UNO work.
- **Exit:** bundled Python version known; a real Claude reply printed from bundled Python.

### Phase 1 — Core Claude client module (1 day)
- `src/claude_client.py`: zero-dependency `call_claude(...)` (timeouts, retry on
  429/500/502/503/529 honoring `retry-after`, 401/400 handling, `content`-block join,
  `stop_reason` check) + `call_claude_async(...)` worker wrapper.
- Unit-test standalone against the real API (outside LibreOffice first).
- **Exit:** robust, tested client that never blocks and degrades gracefully on errors.

### Phase 2 — Calc rewrite-selection end-to-end via APSO macro (1–2 days)  ⟵ *first app (locked)*
- A plain Python macro (no packaging yet): read `getCurrentSelection()` → classify
  SheetCell / SheetCellRange / SheetCellRanges → `getDataArray()` → `call_claude` on a worker
  thread (JSON range + instruction in, same-shaped JSON matrix out) → validate shape,
  coerce `None`/`null`→`""`, ints→floats → `setDataArray()` on the main thread.
- Prompt design: instruct Claude to return a JSON 2-D array of the exact same dimensions.
- **Exit:** select a range in Calc, run macro with an instruction, results written back in one
  `setDataArray` call. The core demo.

### Phase 3 — Writer rewrite-selection (1 day)
- Same shape, Writer model: `getCurrentSelection()`/view-cursor → `call_claude` →
  `insertString(range, text, bAbsorb=True)` to replace (or `False` at caret). Undo context;
  multi-paragraph output via `PARAGRAPH_BREAK`.
- **Exit:** select text in Writer, run macro, Claude rewrites it in place.

### Phase 4 — Package as `.oxt` (1–2 days)
- `description.xml`, `META-INF/manifest.xml`, `Addons.xcu` (Claude menu + toolbar, scoped to
  Calc+Writer), `ProtocolHandler.xcu` + the `XDispatchProvider/XDispatch` component,
  `Accelerators.xcu` (Ctrl-shortcuts), icons.
- Build script (zip → `.oxt`); install via `unopkg add`.
- **Exit:** installable extension; menu/toolbar/shortcut fire the Phase 2/3 flows.

### Phase 5 — Config & security (1–2 days)
- Settings dialog; **API-key storage via DPAPI** (`ctypes`, zero-dep); model picker with
  sane defaults (Haiku/Sonnet/Opus); per-feature system prompts.
- **Exit:** no key in source; user configures model + key in-app.

### Phase 6 — Feature depth & polish (ongoing)
- More commands: summarize, translate, fix grammar, generate formula, explain range.
- Sidebar chat panel (`Sidebar.xcu` + `XUIElementFactory`); streaming for long generations;
  style-preserving "surgical" replace; progress/cancel UX; token/cost display.

### Phase 7 — Distribution
- README + license, packaging/versioning, publish to extensions.libreoffice.org; optional
  auto-update feed in `description.xml`.

## Suggested repo layout
```
LibreOffice-Claude-Connector/
├── docs/            RESEARCH.md, BUILD-PLAN.md
├── src/             claude_client.py, connector.py, config.py, keystore_dpapi.py
├── ext/             description.xml, META-INF/manifest.xml, Addons.xcu,
│                    ProtocolHandler.xcu, Accelerators.xcu, icons/
├── scripts/         build_oxt.(ps1|py), install.ps1
└── tests/
```

## Decisions (locked 2026-07-10)
1. **First app → Calc.** Build the Calc range flow first; port to Writer next.
2. **v1 feature → rewrite/transform selection.** Select a range + an instruction → Claude
   transforms it → written back with `setDataArray`. Single vertical slice.
3. **Provider → Claude-native now.** Implement the Anthropic Messages API directly (system
   prompt, streaming, token usage); keep a thin internal seam so a second provider could slot
   in later, but do not build the abstraction yet.
4. **Distribution → public extension** (extensions.libreoffice.org). Implications baked into
   the phases below:
   - **Target Python 3.8/3.9 stdlib only** (lowest common denominator across LO 24.8→25.8) —
     no 3.10+ syntax, no compiled wheels.
   - **Cross-version test matrix**: at least LO 24.8 (Py 3.9) and a current 25.x (Py 3.11).
   - **License** (MPL-2.0, matching localwriter) + icons + polished, secure key UX are v1
     requirements, not afterthoughts.
   - Robust error UX (no stack traces to end users); `description.xml` update feed.
