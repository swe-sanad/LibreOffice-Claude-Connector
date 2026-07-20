# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Agent acceptor — makes every running LibreOffice reachable by local agents.

A Job (bound in Jobs.xcu to the office's global start events) that opens a UNO
acceptor on a per-user **named pipe** from INSIDE the office process — exactly
what `--accept=pipe,...` does, minus the command line. With this installed, a
LibreOffice the user opened normally is connectable at
`uno:pipe,name=lo-claude-<user>;urp;StarOffice.ComponentContext` — no flags,
no ports, no TCP (named pipes / OSL pipes are local-only by construction).

Environment:
    CLAUDE_AGENT_ACCEPTOR=0   disable entirely
    CLAUDE_AGENT_PIPE=<name>  override the pipe name (tests use this)

Failure posture: never a dialog, never block startup or shutdown. Any error
logs to stderr and ends the thread. The Jobs framework may permanently
deactivate a job whose execute() raises, so execute() never raises.
"""

import getpass
import os
import re
import sys
import threading

import unohelper
from com.sun.star.bridge import XInstanceProvider
from com.sun.star.frame import XTerminateListener
from com.sun.star.lang import XServiceInfo
from com.sun.star.task import XJob

IMPL_NAME = "com.swepioneers.claudeconn.AgentAcceptor"
SERVICE_NAME = IMPL_NAME

_state = {"started": False, "acceptor": None}
_lock = threading.Lock()


def _log(msg):
    try:
        sys.stderr.write("[claude-agent-acceptor] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


def default_pipe_name():
    # MUST stay identical to _default_pipe_name() in mcp/libreoffice_mcp.py —
    # this side opens the pipe, the MCP server dials it.
    user = re.sub(r"[^a-z0-9-]", "-", getpass.getuser().lower()) or "user"
    return "lo-claude-" + user


class _CtxProvider(unohelper.Base, XInstanceProvider):
    """Hands the office's context to a freshly bridged client — the same
    contract `--accept` fulfills."""

    def __init__(self, ctx):
        self.ctx = ctx

    def getInstance(self, name):
        if name == "StarOffice.ComponentContext":
            return self.ctx
        if name == "StarOffice.ServiceManager":
            return self.ctx.ServiceManager
        return None


class _Terminator(unohelper.Base, XTerminateListener):
    """Stops the acceptor when the office shuts down so the blocking accept()
    can never keep soffice.bin alive."""

    def queryTermination(self, _event):     # never veto
        pass

    def notifyTermination(self, _event):
        acceptor = _state.get("acceptor")
        if acceptor is not None:
            try:
                acceptor.stopAccepting()
            except Exception:
                pass

    def disposing(self, _event):
        pass


def _accept_loop(ctx, acceptor, bridges, pipe_name):
    accept_str = "pipe,name=%s" % pipe_name
    provider = _CtxProvider(ctx)
    _log("listening on %s" % accept_str)
    n = 0
    errors = 0
    while True:
        try:
            conn = acceptor.accept(accept_str)
            errors = 0
        except Exception as exc:
            # transient (a client that vanished mid-handshake) vs fatal (pipe
            # owned by another instance, teardown): retry a few times, then end
            errors += 1
            _log("accept error %d: %s" % (errors, exc))
            if errors >= 3:
                return
            import time
            time.sleep(0.5)
            continue
        if conn is None:                    # stopAccepting() during shutdown
            _log("acceptor stopped")
            return
        n += 1
        try:
            # empty name -> a fresh anonymous bridge per connection, exactly
            # like --accept. The bridge keeps itself alive on the connection.
            bridges.createBridge("", "urp", conn, provider)
            _log("bridged client #%d" % n)
        except Exception as exc:
            _log("bridge failed for client #%d: %s" % (n, exc))
            try:
                conn.close()                # nobody owns it on bridge failure
            except Exception:
                pass


def start_acceptor(ctx):
    """Idempotent: one acceptor thread per office process."""
    if os.environ.get("CLAUDE_AGENT_ACCEPTOR", "1").strip().lower() in ("0", "false", "no"):
        _log("disabled via CLAUDE_AGENT_ACCEPTOR")
        return False
    with _lock:
        if _state["started"]:
            return True
        _state["started"] = True
    pipe_name = os.environ.get("CLAUDE_AGENT_PIPE") or default_pipe_name()
    # create + PUBLISH the acceptor on the Job thread, before the worker spawns:
    # a terminate arriving in that window can then always stopAccepting()
    smgr = ctx.ServiceManager
    try:
        acceptor = smgr.createInstanceWithContext(
            "com.sun.star.connection.Acceptor", ctx)
        bridges = smgr.createInstanceWithContext(
            "com.sun.star.bridge.BridgeFactory", ctx)
        _state["acceptor"] = acceptor
    except Exception as exc:
        _log("setup failed: %s" % exc)
        return False
    thread = threading.Thread(target=_accept_loop,
                              args=(ctx, acceptor, bridges, pipe_name),
                              name="claude-agent-acceptor", daemon=True)
    thread.start()
    try:
        desktop = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx)
        desktop.addTerminateListener(_Terminator())
    except Exception as exc:
        _log("terminate-listener registration failed (non-fatal): %s" % exc)
    return True


class AgentAcceptorJob(unohelper.Base, XJob, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx

    # -- XJob ------------------------------------------------------------ #
    def execute(self, _args):
        try:
            start_acceptor(self.ctx)
        except Exception as exc:            # a raising job gets deactivated
            _log("execute failed: %s" % exc)
        return ()

    # -- XServiceInfo ------------------------------------------------------ #
    def getImplementationName(self):
        return IMPL_NAME

    def supportsService(self, name):
        return name == SERVICE_NAME

    def getSupportedServiceNames(self):
        return (SERVICE_NAME,)


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    AgentAcceptorJob, IMPL_NAME, (SERVICE_NAME,))
