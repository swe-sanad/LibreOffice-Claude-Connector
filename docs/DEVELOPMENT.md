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
`urllib`), [tests/test_calc_actions.py](../tests/test_calc_actions.py) (17 tests,
covering prompt building, `parse_grid`'s tolerance for fences/prose, dimension-mismatch
errors, and `coerce_out_cell`), and [tests/test_writer_actions.py](../tests/test_writer_actions.py)
(covering `clean_output`'s fence-stripping/quote-preservation, `default_max_tokens`
bounds, and `rewrite_text`/`generate_text` orchestration) are all fully offline — no API
key, no network access, and no running LibreOffice required. Run them with the bundled
interpreter:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s tests -p "test_*.py" -v
```

All **65** currently pass on the bundled Python 3.10.17, including
[tests/test_config_keystore.py](../tests/test_config_keystore.py) — config load/save
defaults-merging, and a real DPAPI encrypt/decrypt round-trip (on Windows) asserting
the API key is never stored in plaintext. (This discovers only the top-level `tests/`
directory; the UNO integration tests under `tests/integration/` are run separately
below, since they require a live LibreOffice instance.)

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

## Running the Writer UNO integration test

[tests/integration/test_writer_uno.py](../tests/integration/test_writer_uno.py) drives a
**real, running LibreOffice Writer** the same way the Calc test drives Calc: it reads
the current selection via the view cursor, replaces it, verifies a `\n` in the
replacement comes back as a real paragraph (not a literal `\n` in one paragraph), and
exercises caret-detection + insert-at-caret when nothing is selected. It needs no
`ANTHROPIC_API_KEY` for the same reason — the Claude call is stubbed.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 -Test tests/integration/test_writer_uno.py
```

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

## Building and installing the extension

[scripts/build_oxt.py](../scripts/build_oxt.py) assembles `dist/claude-connector-
<version>.oxt` from `ext/` + `src/` (version read from `ext/description.xml`):

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" scripts\build_oxt.py
```

[scripts/make_icons.py](../scripts/make_icons.py) (stdlib only) regenerates
`ext/icons/*.png` if you need to change them.

To build, install into a throwaway profile, and verify the extension actually loads
and its dispatch commands resolve — without touching your real LibreOffice profile —
run [scripts/install_and_verify.ps1](../scripts/install_and_verify.ps1):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_and_verify.ps1
```

It builds the `.oxt`, `unopkg add`s it into an isolated `-env:UserInstallation` profile
under `%TEMP%`, boots headless LibreOffice **twice** (see the warm-up-boot gotcha
below), then runs [tests/integration/test_extension_dispatch.py](../tests/integration/test_extension_dispatch.py)
against the second boot to confirm both `com.swepioneers.claudeconnector:Transform` and
`:Settings` resolve via `queryDispatch`. Adjust `-Port`/`-LOProgram` the same way as
`run_integration.ps1`.

## Gotchas

### LibreOffice caches Python modules

LibreOffice caches imported Python modules for the lifetime of its process. If you edit
`src/claude_client.py` (or any module loaded by a macro) while LibreOffice is open,
**restart LibreOffice** before re-running a macro — otherwise you will silently keep
executing the old, cached version of the code.

### A UNO text cursor doesn't collapse after an insert — you must do it yourself

Discovered by the Writer integration test, not by any mocked/offline test: after calling
`XText.insertString(...)` or `XText.insertControlCharacter(...)`, the cursor you passed
in still *spans* the text you just inserted — it does not collapse to the end of the
insertion the way you'd assume from, say, a text-editor cursor. If you then insert
again at that same cursor without collapsing it first, the next insert lands at the
*start* of the previous span, not after it.

Concretely, inserting the two-line string `"Line one\nLine two"` paragraph-by-paragraph
without collapsing came out as the paragraphs `['', 'Line twoLine one']` — reversed and
concatenated — instead of `['Line one', 'Line two']`. The fix is one line,
`cursor.collapseToEnd()`, called after every single insert (see
`uno_bridge._insert_multiline`).

**The broader lesson:** this class of bug is invisible to mocked/offline unit tests,
because a mock UNO object doesn't reproduce the real cursor-spanning behavior — the
test would pass against a fake and fail silently in the real application. Any code path
that mutates a UNO document (inserts, cursor moves, multi-step edits) needs at least one
test that exercises it against a **real, running LibreOffice** — which is exactly what
`scripts/run_integration.ps1` + `tests/integration/*.py` are for. Don't trust offline
green checkmarks alone for UNO document-edit logic.

### An installed extension only activates on the NEXT boot — you need a warm-up boot

`unopkg add` registers the extension's files, but LibreOffice only wires up the new
`Addons.xcu` menu/toolbar entries and `ProtocolHandler.xcu` registration into its
running configuration on the *next* start after install — the same "restart to apply"
behavior a real user sees via the Extension Manager. `install_and_verify.ps1` therefore
boots headless LibreOffice once just to let it pick up the registration ("warm-up
boot"), terminates it, waits for the UNO socket to fully close, and only then boots a
second time to actually run the dispatch-resolution check. Skipping the warm-up boot
is the single most common way to get a false "extension not found" failure.

### Killing leftover headless `soffice` instances between boots

A test-only headless `soffice` process launched with a unique `-env:UserInstallation`
profile can be told apart from the developer's real, visible LibreOffice window: match
`Win32_Process` entries whose `CommandLine` contains the test profile's unique marker
(see `Kill-TestOffice` in `install_and_verify.ps1`) rather than killing by process name
alone, which would also kill a real open LibreOffice window. Terminate the office
cleanly first via a `Desktop.terminate()` UNO call over the socket before falling back
to `Stop-Process -Force`, so file locks and the profile directory are released
properly.

### Wait for the UNO port to actually close between boots

After terminating the warm-up boot, the UNO socket doesn't necessarily release
immediately — starting the next boot on the same port too early can either fail to
bind or connect to a half-shutdown process. `install_and_verify.ps1`'s
`Wait-PortClosed` polls the port with a `TcpClient` connect attempt until it starts
failing (meaning nothing is listening any more) before launching the next boot, with a
generous timeout budget (240s cold start / 40s shutdown) — headless cold starts on a
loaded CI/dev machine are not fast.

## APSO

[APSO](https://gitlab.com/JBFSoftware/apso) (Alternative Script Organizer for Python)
is a LibreOffice extension that gives you a Python console and script runner inside
LibreOffice itself. It is not part of this repository, but installing it via
LibreOffice's Extension Manager makes iterating on macros substantially faster during
development — see the Phase 0 note in [BUILD-PLAN.md](BUILD-PLAN.md).


## Regenerating docs/MCP-TOOLS.md

The MCP tool reference is generated from `TOOL_DEFS` in `mcp/libreoffice_mcp.py`
(section comments become headings). After adding or changing tools, re-run:

```powershell
python scripts/gen_mcp_tools_doc.py
```

and commit the refreshed `docs/MCP-TOOLS.md` together with the server change.
