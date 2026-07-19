# Toward native agent support in LibreOffice

Goal: a user (or an AI agent) should be able to connect Claude to LibreOffice with
**zero ceremony** — no `--accept=socket,...` flags, no ports, ideally nothing to
install. This document maps the road from what we ship today to a real LibreOffice
core contribution.

## Rung 0 — shipped in v0.6.0: the server launches LibreOffice itself

The MCP server no longer requires the user to start LibreOffice with socket flags:
on a failed connect it finds `soffice` (next to its own interpreter, `LO_SOFFICE`,
or the standard install paths), launches it with the UNO accept argument, and
retries. Cold start → working connection with no setup at all.

Remaining gap: if LibreOffice is **already running without a listener** (the user
opened it normally), the single-instance mechanism swallows our launch and the
accept argument is ignored. That gap is exactly what Rung 1 closes.

## Rung 1 — an extension that makes every running LibreOffice reachable

No core changes needed. A small `.oxt` (natural addition to this repo, since we
already build one) that:

1. Registers a **Job** on the `OnStartApp` event.
2. On startup, creates a UNO **Acceptor** (`com.sun.star.connection.Acceptor`) on a
   **named pipe** (`pipe,name=libreoffice-claude-<user>`) and wires it to a
   `BridgeFactory`, exactly what `--accept` does — but from *inside* the running
   process, so it works no matter how LibreOffice was started.
3. The MCP server then connects via `uno:pipe,name=...` first, falling back to the
   TCP socket, falling back to auto-launch (Rung 0).

With this extension installed, **every** LibreOffice instance is agent-reachable
the moment it starts. A settings toggle (default on, per-user pipe name, no
network exposure — named pipes are local-only) addresses the security posture.
Distribute via extensions.libreoffice.org for one-click install.

## Rung 2 — the actual core contribution

What to propose to The Document Foundation: **a built-in, opt-in local agent
endpoint** — essentially Rung 1 living in core with a real UI:

- *Tools ▸ Options ▸ General ▸ "Allow local AI agents to control this office"*
  (default **off**; per-user named pipe / unix domain socket; never TCP by default).
- Ideally speaking **MCP natively** (stdio child transport or local socket) so any
  agent — Claude, or anything else — connects without a translator process. The
  tool surface can be generated from the same UNO introspection this repo uses.
- Security model to propose up front (this will be the main discussion): explicit
  opt-in, local-only transport, a per-document permission prompt for macro
  execution, and an audit trail of agent actions (our `audit_log` pattern).

### The TDF process, concretely

1. **RFC first, code second.** Post the proposal to the developer list
   (`libreoffice@lists.freedesktop.org`) and bring it to an **ESC call**
   (Engineering Steering Committee, weekly). Reference this repo as the working
   prototype with real-world usage — a working MCP server with 61 tools and a
   production workbook built through it is strong evidence.
2. **Start with the smallest reviewable patch**: the Options toggle + in-process
   acceptor (Rung 1's logic in `desktop/`/`sfx2/`, C++). Submit via
   **gerrit.libreoffice.org** (create account, `logerrit` setup, one logical
   change per patch; a `tdf#` Bugzilla ticket of type *enhancement* anchors it).
3. Native MCP in core is a bigger conversation (new protocol dependency) — expect
   it to become a GSoC-sized project or a TDF tender; the RFC can propose it as
   phase 2 while the acceptor toggle lands as phase 1.
4. License: LibreOffice core is MPL-2.0/LGPLv3+ — this repo is already MPL-2.0,
   so code can move upstream without relicensing friction.

## Practical order

| Step | Where | Effort | Unblocks |
|---|---|---|---|
| Auto-launch (done, v0.6.0) | this repo | — | cold-start zero ceremony |
| Pipe-acceptor `.oxt` + pipe-first connect | this repo | days | already-running LibreOffice |
| extensions.libreoffice.org listing | TDF site | form + review | one-click install for everyone |
| RFC + ESC + Bugzilla enhancement | TDF | weeks of discussion | legitimacy, direction |
| Options toggle + acceptor patch | gerrit, C++ | small patch series | native, no extension |
| Native MCP endpoint | gerrit / GSoC / tender | large | the end state |
