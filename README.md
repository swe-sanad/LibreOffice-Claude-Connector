# LibreOffice ↔ Claude Connector

An open-source LibreOffice extension that embeds Anthropic's **Claude** directly into
**Calc** (first) and **Writer**, via the UNO API and LibreOffice's own bundled Python
interpreter — no external runtime, no third-party Python packages. Select a range or
some text, give Claude an instruction, and get the transformed result written straight
back into your document.

## Status

**Phase 2 complete: Calc read → transform → write, verified against real LibreOffice.**
Selecting a range in Calc, sending it to Claude, and writing the transformed result back
with `setDataArray` now works end to end (the Claude call itself is currently wired
through a deterministic stub in the integration test; a real API key just needs to be
set for it to call Claude for real). Writer integration and the packaged `.oxt`
extension UI are next. See [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) for the full
7-phase plan and [docs/RESEARCH.md](docs/RESEARCH.md) for the underlying research (UNO
API, LibreOffice's bundled Python, the Claude Messages API, and prior-art review).

## Features

- [x] Zero-dependency Claude Messages API client (stdlib `urllib` + `json` + `ssl` only)
- [x] Typed error handling, retries with backoff, `retry-after` support
- [x] Calc: select a range, transform it with Claude, write results back
- [ ] Writer: select text, rewrite it with Claude, replace in place
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

These **31** tests (14 for the Claude client, 17 for the Calc transform logic) mock
`urllib`/use fake clients entirely — no API key, no network access, and no running
LibreOffice needed.

To exercise the Calc UNO read/write path against a real, isolated headless LibreOffice
(no API key needed — the Claude call is a deterministic stub):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_calc_uno.py
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
│                    uno_bridge.py (UNO glue: selection read/write for Calc)
├── ext/             extension scaffold (description.xml, META-INF/, icons/, pythonpath/, registry/)
├── scripts/         spike_http.py (live Claude smoke test)
│                    run_integration.ps1 (launches isolated headless LO, runs a UNO integration test)
└── tests/           test_claude_client.py, test_calc_actions.py (offline unit tests)
                     integration/test_calc_uno.py (live UNO integration test)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — technical design and layering
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — how to develop and test
- [docs/RESEARCH.md](docs/RESEARCH.md) — fact-checked research (architecture, UNO API, prior art, risks)
- [docs/BUILD-PLAN.md](docs/BUILD-PLAN.md) — the 7-phase build plan and locked decisions
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — release history

## License

[MPL-2.0](LICENSE) — Mozilla Public License, Version 2.0.
