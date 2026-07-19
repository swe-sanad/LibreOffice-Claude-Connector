# Plan — Rung 1/2: the pipe-acceptor extension

**Goal:** every running LibreOffice is reachable by the MCP server the moment it
starts — including instances the user opened normally — with no flags, no ports,
no TCP. Closes the single remaining connect gap after v0.6.0's auto-launch.

**Shape:** a new component inside our existing `.oxt` (we already build, install
and verify one) that starts a UNO acceptor on a **local named pipe** at office
startup, plus a pipe-first connect path in the MCP server.

## Phase A — in-process acceptor component (the core)

| # | Task | Where | Notes |
|---|------|-------|-------|
| A1 | `pythonpath/claudeconn/agent_acceptor.py`: a `com.sun.star.task.XJob` implementation | ext/ | registered like `connector.py`'s ProtocolHandler |
| A2 | `Jobs.xcu`: bind the job to the **`onFirstVisibleTask`** event (fires once per office start, UI ready) | ext/ | `OnStartApp` alternatives: test which fires reliably under both GUI and headless |
| A3 | Job body: spawn a **daemon thread** that creates `com.sun.star.connection.Acceptor`, loops `accept("pipe,name=<PIPE>")`, and for each connection calls `com.sun.star.bridge.BridgeFactory.createBridge("", "urp", conn, instanceProvider)` | ext/ | the instance provider returns the office `ComponentContext` under the name `StarOffice.ComponentContext` — byte-for-byte what `--accept` does internally |
| A4 | Pipe name: `lo-claude-<sanitized-username>` — per-user, local-only (Windows named pipes and POSIX sockets are not network-reachable) | ext/ | no TCP by default = the security story |
| A5 | Idempotence + teardown: skip if an acceptor already exists (second window, quickstarter); stop the thread on `OnCloseApp` | ext/ | a stray acceptor thread must never block office shutdown — daemon + `acceptor.stopAccepting()` |
| A6 | Opt-out: registry config `EnableAgentAccess` (default **on**) + a `Claude ▸ Agent access` menu check-item toggling it | ext/ | ships default-on because pipe is local; flip default to off if extensions.libreoffice.org review pushes back |

## Phase B — MCP server: pipe-first connect ladder

| # | Task | Where |
|---|------|-------|
| B1 | `uno_bridge.connect_url(url)` accepting a full UNO URL; try in order: `uno:pipe,name=<PIPE>;urp;StarOffice.ComponentContext` → `uno:socket,host=localhost,port=<LO_UNO_PORT>;urp;...` → auto-launch (existing) | src/, mcp/ |
| B2 | `LO_UNO_PIPE` env override; report which transport connected in `lo_status` | mcp/ |
| B3 | Auto-launch keeps using the socket (a launched instance has no extension guarantee) | mcp/ |

## Phase C — proof & guardrails

| # | Task | Notes |
|---|------|-------|
| C1 | Integration test: install the `.oxt` into the isolated LO profile (existing `run_integration.ps1` harness), start soffice **without any accept flag**, connect via pipe, read a cell | the acceptance test for the whole feature |
| C2 | Test the swallow case: office already running *with* extension + second manual launch → still one pipe, still connectable | |
| C3 | Windows + Linux CI paths for the pipe name (pipe on Win, same UNO pipe abstraction on Linux — LibreOffice maps it to an abstract socket in `/tmp/OSL_PIPE_*`) | |
| C4 | `docs/SECURITY.md`: local-only transport, per-user pipe, opt-out, what an agent can do once connected (everything the user can — say it plainly) | needed for extensions.libreoffice.org AND the Anthropic directory review |

## Phase D — distribution

| # | Task | Notes |
|---|------|-------|
| D1 | Bump `.oxt` to 0.2.0; release artifact | existing build script |
| D2 | **extensions.libreoffice.org** listing: create account, submit `.oxt` with description, icon, screenshots, MIT; expect a light human review | this is the "one-click for everyone" moment |
| D3 | README + UPSTREAMING update: connect ladder becomes pipe → socket → auto-launch | |

**Risks:** (1) Python-in-extension acceptor thread stability across LO versions —
mitigate with the C1 integration test on 24.8/25.x; (2) event choice
(`onFirstVisibleTask` vs `OnStartApp`) behaves differently headless — test both;
(3) marketplace review may dislike default-on — the config toggle (A6) is the
pressure valve. **Estimate: 3–5 working days** including tests and the listing.
