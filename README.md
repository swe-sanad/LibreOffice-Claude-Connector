# LibreOffice ↔ Claude Connector

An open-source LibreOffice extension that embeds Anthropic's **Claude** directly into
**Calc** (first) and **Writer**, via the UNO API and LibreOffice's own bundled Python
interpreter — no external runtime, no third-party Python packages. Select a range or
some text, give Claude an instruction, and get the transformed result written straight
back into your document.

## Status

**Phase 3 complete: Calc and Writer both verified end to end against real LibreOffice.**
Selecting a range in Calc, sending it to Claude, and writing the transformed result back
with `setDataArray` works end to end, and Writer now does the same for text: select some
text and rewrite it in place, or place the caret with nothing selected and generate new
text there — both via the view cursor, with multi-paragraph replies turned into real
paragraph breaks and grouped into a single undo step. (The Claude call itself is
currently wired through a deterministic stub in the integration tests; a real API key
just needs to be set for either to call Claude for real.) The packaged `.oxt` extension
UI is next. See [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) for the full 7-phase plan and
[docs/RESEARCH.md](docs/RESEARCH.md) for the underlying research (UNO API, LibreOffice's
bundled Python, the Claude Messages API, and prior-art review).

## Features

- [x] Zero-dependency Claude Messages API client (stdlib `urllib` + `json` + `ssl` only)
- [x] Typed error handling, retries with backoff, `retry-after` support
- [x] Calc: select a range, transform it with Claude, write results back
- [x] Writer: select text and rewrite it with Claude in place, or generate new text at
      the caret when nothing is selected
- [ ] Packaged `.oxt` extension with menu/toolbar/shortcut
- [ ] In-app settings (model picker, API key via Windows DPAPI)
- [ ] Additional commands (summarize, translate, fix grammar, generate formula, explain range)
- [ ] Public release on extensions.libreoffice.org

## Requirements

- LibreOffice (24.8 or later) — this project targets LibreOffice's **bundled Python**
  interpreter, not a system Python. No `pip install` is required or supported inside
  that interpreter.
- Code targets **Python 3.8+ standard library only**, so it runs unmodified across the
  bundled interpreters shipped with LibreOffice 24.8 → 25.8 (Python 3.9 → 3.11).

## Development

Run the offline unit tests with LibreOffice's bundled Python (adjust the path for your
LibreOffice install):

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

These **42** tests (14 for the Claude client, 17 for the Calc transform logic, plus
the Writer transform/prompt-building logic) mock `urllib`/use fake clients entirely —
no API key, no network access, and no running LibreOffice needed.

To exercise the Calc UNO read/write path against a real, isolated headless LibreOffice
(no API key needed — the Claude call is a deterministic stub):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_calc_uno.py
```

To exercise the same for Writer (selection read/replace, multi-paragraph inserts,
caret-insert):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_writer_uno.py
```

To prove live connectivity (TLS, headers, error parsing) from the bundled interpreter,
set `ANTHROPIC_API_KEY` and run the smoke-test spike:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."
& "C:\Program Files\LibreOffice\program\python.exe" scripts\spike_http.py
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full development workflow,
including the "LibreOffice caches Python modules" gotcha.

## Project layout

```
LibreOffice-Claude-Connector/
├── docs/            RESEARCH.md, BUILD-PLAN.md, ARCHITECTURE.md, DEVELOPMENT.md, CHANGELOG.md
├── src/             claude_client.py (Claude Messages API client)
│                    calc_actions.py (pure Calc transform/prompt/parse logic)
│                    writer_actions.py (pure Writer rewrite/generate/prompt logic)
│                    uno_bridge.py (UNO glue: selection read/write for Calc and Writer)
├── ext/             extension scaffold (description.xml, META-INF/, icons/, pythonpath/, registry/)
├── scripts/         spike_http.py (live Claude smoke test)
│                    run_integration.ps1 (launches isolated headless LO, runs a UNO integration test)
└── tests/           test_claude_client.py, test_calc_actions.py, test_writer_actions.py (offline unit tests)
                     integration/test_calc_uno.py, integration/test_writer_uno.py (live UNO integration tests)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — technical design and layering
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — how to develop and test
- [docs/RESEARCH.md](docs/RESEARCH.md) — fact-checked research (architecture, UNO API, prior art, risks)
- [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) — the 7-phase build plan and locked decisions
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — release history

## License

[MPL-2.0](LICENSE) — Mozilla Public License, Version 2.0.
