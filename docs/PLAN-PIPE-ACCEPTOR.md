# Plan — `.oxt` 0.2.0: the agent-acceptor extension (concrete, file-by-file)

**Goal:** every running LibreOffice is reachable by the MCP server the moment it
starts — including instances the user opened normally — with no flags, no ports,
no TCP. Closes the one gap left after v0.6.0's auto-launch (a listenerless
already-running office swallows our launch).

**Mechanism:** a Job inside our existing `.oxt` opens a UNO acceptor on a
**local named pipe** from *inside* the office process at startup — byte-for-byte
what `--accept=pipe,...` does, minus the command line. The MCP server then
connects pipe-first.

Everything below is grounded in the current scaffold: `build_oxt.py`'s
`ROOT_COMPONENTS`/`PACKAGE_MODULES` registry, the `META-INF/manifest.xml`
component entries, and the `run_integration.ps1` / `install_and_verify.ps1`
isolated-profile harnesses.

---

## Step 0 — the failing test first (project TDD convention)

`tests/integration/test_agent_acceptor.py`, run via the existing isolated
harness, asserting the END state:

1. Build the `.oxt`; `unopkg add` it into an **isolated profile**
   (`-env:UserInstallation=file:///<temp>` — `install_and_verify.ps1` already
   knows this dance; add a `-KeepProfile`/reuse switch or a small sibling
   script `run_acceptor_test.ps1`).
2. Start `soffice` from that profile **with NO `--accept` argument** (plus one
   warm-up boot first — extension activation is next-boot, per DEVELOPMENT.md).
3. From LibreOffice python, resolve
   `uno:pipe,name=lo-claude-<user>;urp;StarOffice.ComponentContext`
   (retry ~20×0.5s — the Job fires after the first frame).
4. Read+write a cell through the pipe bridge. Kill soffice.

Run it now → **red** (no acceptor exists). Everything after exists to turn it
green.

## Step 1 — the acceptor component

**New file `src/agent_acceptor.py`** (≈120 lines), bundled at the oxt ROOT as a
registered Python UNO component (3rd entry in `ROOT_COMPONENTS`):

- `class AgentAcceptorJob(unohelper.Base, XJob)` — implementation name
  `com.swepioneers.claudeconn.AgentAcceptor`, registered via
  `g_ImplementationHelper` exactly like `connector.py`.
- `execute(args)` (the Job entry): idempotence guard (module-level flag — the
  event can fire once per new frame), then start ONE **daemon thread**:

  ```python
  class _CtxProvider(unohelper.Base, XInstanceProvider):
      def __init__(self, ctx): self.ctx = ctx
      def getInstance(self, name):          # client asks for
          return self.ctx                   # "StarOffice.ComponentContext"

  acceptor = smgr.createInstanceWithContext(
      "com.sun.star.connection.Acceptor", ctx)
  bridges = smgr.createInstanceWithContext(
      "com.sun.star.bridge.BridgeFactory", ctx)
  accept_str = "pipe,name=" + pipe_name          # lo-claude-<sanitized user>
  while not stopping:
      conn = acceptor.accept(accept_str)         # blocks; None on stopAccepting
      if conn is None: break
      bridges.createBridge("", "urp", conn, _CtxProvider(ctx))
  ```

- Pipe name: `lo-claude-` + `getpass.getuser()` sanitized `[a-z0-9-]` —
  per-user, local-only (Windows named pipe / `/tmp/OSL_PIPE_*` on Linux — not
  network-reachable). `CLAUDE_AGENT_PIPE` env respected for tests.
- Failure posture: if `accept()` raises (pipe already owned — e.g. a second
  isolated instance), log to stderr and end the thread quietly. Never a dialog,
  never block office startup or shutdown (daemon thread + `stopAccepting()` in
  a `terminate` listener via `XTerminateListener` on the Desktop).
- Opt-out for 0.2.0 kept **ponytail-simple**: env `CLAUDE_AGENT_ACCEPTOR=0`
  disables. (The registry-config + menu check-item toggle is 0.2.1 — noted in
  Deferred, don't build it now.)

**New file `ext/Jobs.xcu`** — bind the Job to office startup:

```xml
<node oor:name="Jobs">
  <node oor:name="ClaudeAgentAcceptor" oor:op="replace">
    <prop oor:name="Service">
      <value>com.swepioneers.claudeconn.AgentAcceptor</value></prop>
  </node>
</node>
<node oor:name="Events">
  <node oor:name="onFirstVisibleTask" oor:op="fuse">
    <node oor:name="JobList">
      <node oor:name="ClaudeAgentAcceptor" oor:op="replace"/>
    </node></node>
  <!-- also bind OnStartApp: onFirstVisibleTask never fires headless -->
  <node oor:name="OnStartApp" oor:op="fuse"> ... same JobList ... </node>
</node>
```

(Exact schema: `org.openoffice.Office.Jobs` — crib from any AOO/LO Jobs.xcu
sample; test BOTH events fire-once behavior in Step 0's test, GUI and
`--headless` variants.)

**Edits:**
- `ext/META-INF/manifest.xml`: two entries — `agent_acceptor.py`
  (`uno-component;type=Python`) and `Jobs.xcu` (`configuration-data`).
- `scripts/build_oxt.py`: `ROOT_COMPONENTS = ["connector", "sidebar_panel",
  "agent_acceptor"]` (the `ext/` walk picks up `Jobs.xcu` automatically).
- `ext/description.xml`: version `0.1.0` → `0.2.0`.

## Step 2 — MCP server: pipe-first connect ladder

- `src/uno_bridge.py`: generalize `connect()` with a
  `resolve(url, retries, delay)` core; new `connect_any(pipe_name, port, ...)`
  trying **pipe → socket** and returning `(ctx, smgr, desktop, transport)`.
- `mcp/libreoffice_mcp.py` `_connect()` ladder becomes:
  1. `uno:pipe,name=<LO_UNO_PIPE or lo-claude-<user>>` (1 quick try — cheap)
  2. `uno:socket,host=localhost,port=<LO_UNO_PORT>` (existing)
  3. auto-launch with the socket accept arg (existing — a freshly launched
     instance has no extension guarantee), then retry socket.
  Cache `_state["transport"]`; `tool_lo_status` reports it
  (`"transport": "pipe" | "socket"`).
- `SERVER_VERSION` → `0.7.0`; regen `docs/MCP-TOOLS.md`; `scripts/
  test_mcpb_bundle.py --live` must stay green (it exercises the socket path).

## Step 3 — proof

| Check | Command |
|---|---|
| Acceptor integration test (the Step-0 test, now green) | `powershell -File scripts/run_integration.ps1 -Test tests/integration/test_agent_acceptor.py` (or its sibling script) |
| Headless variant (OnStartApp binding) | same test with `-Headless` |
| Swallow case fixed end-to-end | manual: open LibreOffice normally (no flags), then `python scripts/test_mcpb_bundle.py --live` → `lo_status` shows `"transport": "pipe"` |
| No regression cold-start | close all soffice; bundle test again → `"transport": "socket"` via auto-launch |
| Store/shutdown safety | open+close office 3× with extension installed; no hang (the old shape.Anchor hang taught us to test store/exit paths explicitly) |

## Step 4 — docs + release

- `docs/SECURITY.md`: what the acceptor is, local-only transport, per-user pipe
  name, `CLAUDE_AGENT_ACCEPTOR=0` opt-out, and the plain sentence: *a connected
  agent can do anything the user can do in LibreOffice*. Link it from README +
  the Anthropic dossier.
- README: connect ladder becomes pipe → socket → auto-launch; UPSTREAMING.md
  Rung 1 marked shipped.
- Release **v0.7.0**: `claude-connector-0.2.0.oxt` +
  `libreoffice-connector-0.7.0.mcpb` (remember: `gh auth switch --user
  swe-sanad` immediately before pushing).
- **extensions.libreoffice.org** listing (user account required): submit the
  0.2.0 `.oxt` — name, description (desc_en.txt), MIT, `icons/icon.png`,
  2–3 screenshots (sidebar + a transform + the acceptor settings note),
  link to SECURITY.md. Expect a light human review.

## Deferred (explicitly NOT in 0.2.0)

- Registry-config toggle + `Claude ▸ Agent access` menu item (0.2.1; env var
  suffices to ship).
- macOS/Linux CI runs of the integration test (manual spot-check only for now).

## Risks & mitigations

1. **Event choice**: `onFirstVisibleTask` doesn't fire headless; `OnStartApp`
   timing differs across versions → bind both, idempotence guard makes double
   fire harmless; the test matrix covers GUI + headless.
2. **PyUNO acceptor stability across LO 24.8→25.x**: the Step-0 test IS the
   canary; run it against both installed versions before release.
3. **Office shutdown hangs**: daemon thread + `stopAccepting()` on terminate;
   the 3×-open/close check is in the proof table because a hang here bricks
   the user's office, not our tool.
4. **Marketplace review objects to default-on**: the pressure valve is
   flipping the default in a 0.2.1 with the config toggle — a one-line change
   either way.

**Estimate:** 3–5 focused sessions: (1) red test + harness plumbing,
(2) acceptor component green, (3) server ladder + regression suite,
(4) docs + release, (5) listing submission + review follow-ups.
