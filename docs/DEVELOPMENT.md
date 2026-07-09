# Development Guide

How to set up, test, and iterate on this project on Windows. For the technical design
see [ARCHITECTURE.md](ARCHITECTURE.md); for the phased roadmap see [BUILD-PLAN.md](BUILD-PLAN.md).

## Why the bundled Python

LibreOffice ships its own Python interpreter, separate from any system Python, and its
macro/scripting environment (UNO) only runs inside that interpreter. It has **no `pip`**
and no third-party packages available, which is why [src/claude_client.py](../src/claude_client.py)
is written against the standard library only. All testing described below is done
against that same bundled interpreter so results are representative of the real
runtime, not a developer's system Python.

On Windows, the bundled interpreter is typically at:

```
C:\Program Files\LibreOffice\program\python.exe
```

Adjust the path if LibreOffice is installed elsewhere. You can confirm the version with:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" --version
```

(Verified in Phase 1: LibreOffice 25.2.3.2 bundles Python 3.10.17.)

## Running the unit tests

[tests/test_claude_client.py](../tests/test_claude_client.py) (14 tests, mocking
`urllib`) and [tests/test_calc_actions.py](../tests/test_calc_actions.py) (17 tests,
covering prompt building, `parse_grid`'s tolerance for fences/prose, dimension-mismatch
errors, and `coerce_out_cell`) are both fully offline — no API key, no network access,
and no running LibreOffice required. Run them with the bundled interpreter:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

All **31** currently pass on the bundled Python 3.10.17. (This discovers only the
top-level `tests/` directory; the UNO integration test under `tests/integration/` is
run separately below, since it requires a live LibreOffice instance.)

## Running the Calc UNO integration test

[tests/integration/test_calc_uno.py](../tests/integration/test_calc_uno.py) drives a
**real, running LibreOffice** over UNO — it is not a mock. It requires no
`ANTHROPIC_API_KEY`: the Claude call is replaced by a deterministic stub transform
(uppercase text, `+1` to numbers), so it exercises only the UNO
read-selection/normalize/write path (`src/uno_bridge.py`).

[scripts/run_integration.ps1](../scripts/run_integration.ps1) launches an **isolated**
headless LibreOffice instance (its own user profile under `%TEMP%`, its own UNO socket
port) so it never disturbs a LibreOffice window you already have open, waits for the
UNO socket to come up, runs the test, then terminates that instance:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_calc_uno.py
```

Run with no `-Test` argument, it defaults to `tests/integration/test_calc_uno.py`. Adjust
`-LOProgram` if LibreOffice is installed somewhere other than
`C:\Program Files\LibreOffice\program`, and `-Port` if 2002 is already in use.

## Running the live smoke test

[scripts/spike_http.py](../scripts/spike_http.py) makes one real HTTPS call to
`api.anthropic.com/v1/messages` from the bundled interpreter, to prove TLS,
reachability, headers, and error parsing work out of the box on Windows. It requires
`ANTHROPIC_API_KEY` to be set:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."
& "C:\Program Files\LibreOffice\program\python.exe" scripts\spike_http.py
```

Without a valid key, the script still confirms connectivity: a real request with an
invalid/absent key correctly returns HTTP 401, which the client maps to
`ClaudeAuthError` — this was verified in Phase 1 and demonstrates the whole request/
error-parsing path works before a real key is even configured.

**Never commit an API key.** Set it as a local/user environment variable
(`setx ANTHROPIC_API_KEY ...`), not in a file tracked by git.

## Gotcha: LibreOffice caches Python modules

LibreOffice caches imported Python modules for the lifetime of its process. If you edit
`src/claude_client.py` (or any module loaded by a macro) while LibreOffice is open,
**restart LibreOffice** before re-running a macro — otherwise you will silently keep
executing the old, cached version of the code.

## APSO

[APSO](https://gitlab.com/JBFSoftware/apso) (Alternative Script Organizer for Python)
is a LibreOffice extension that gives you a Python console and script runner inside
LibreOffice itself. It is not part of this repository, but installing it via
LibreOffice's Extension Manager makes iterating on macros substantially faster during
development — see the Phase 0 note in [BUILD-PLAN.md](BUILD-PLAN.md).
