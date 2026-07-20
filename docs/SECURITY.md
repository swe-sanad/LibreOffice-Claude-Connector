# Security model

This project connects an AI agent (Claude) to LibreOffice. That is powerful, so
the security posture is deliberately simple and honest.

## What an agent can do once connected

**Everything the user can do in LibreOffice.** The tools read and write
documents, run embedded Basic macros (`run_macro`), and can execute arbitrary
Python against the office via `uno_exec`. There is no capability sandbox — the
escape hatch is an intentional feature of the product. Treat granting an agent
access the same way you would treat handing someone your keyboard.

There is **no telemetry** and **no network egress** from the MCP server itself:
it is standard-library-only Python talking to LibreOffice over a local
transport. (The separate in-app `.oxt` extension calls the Claude API only when
*you* invoke a Claude menu command, using *your* API key — see the README.)

## Transports, weakest-binding-last

The MCP server reaches LibreOffice over one of three local transports, tried in
order. **None of them is network-reachable by default.**

1. **Named pipe** (`uno:pipe,name=lo-claude-<user>`) — opened by the
   agent-acceptor extension (below). A Windows named pipe / a POSIX `OSL_PIPE`
   socket is local to the machine and scoped to the OS session; it is **not a
   TCP port** and cannot be reached from the network.
2. **Loopback socket** (`uno:socket,host=localhost,port=<LO_UNO_PORT>`) — the
   classic `--accept` channel, bound to `localhost` only. Reachable by any local
   process that knows the port, same as any developer UNO socket. Used only when
   the pipe isn't available.
3. **Auto-launch** — if nothing is listening, the server starts LibreOffice
   itself with the loopback socket accept argument.

`lo_status` reports which transport connected and the office's profile path, so
you can always see *which* LibreOffice answered.

## The agent-acceptor extension (`.oxt`)

The extension runs a Job at office startup that opens the **named pipe** from
inside the LibreOffice process — byte-for-byte what `--accept=pipe,...` does,
minus the command line — so a LibreOffice you opened normally becomes reachable
without any flags.

- **Local-only:** named pipe / OSL pipe, never TCP. Not exposed to the network.
- **Per-user pipe name** (`lo-claude-<sanitized-username>`): a different OS user
  gets a different pipe.
- **Does not keep the office alive:** the acceptor runs on a daemon thread and
  registers a terminate listener that stops accepting on shutdown. When you
  close your last window, LibreOffice exits normally — verified by a 3×
  open/close self-exit test.
- **Opt-out:** set the environment variable `CLAUDE_AGENT_ACCEPTOR=0` to disable
  the acceptor entirely. `CLAUDE_AGENT_PIPE=<name>` overrides the pipe name.
  (A GUI toggle under a Claude menu is planned for a later release; the env var
  is the mechanism today.)

## Reporting

Security issues: open a GitHub issue, or email the address in the manifest
`author` field. This is a personal open-source project (MIT); there is no
formal SLA, but reports are welcome and will be addressed.
