# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LibreOffice MCP server — JSON-RPC 2.0 over stdio, stdlib only.

This module is the PROTOCOL layer and nothing else: initialize, tools/list,
tools/call, prompts, and the stdin loop. The tools live in ``loconn/tools/``
(one module per application per concern) and register themselves through
``loconn.registry``; the shared UNO machinery is in ``loconn/core.py``.

Run it under LibreOffice's own Python — the only interpreter shipping ``uno``::

    "C:/Program Files/LibreOffice/program/python.exe" mcp/libreoffice_mcp.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loconn.core import *                    # noqa: E402,F401,F403
from loconn import tools as _tools           # noqa: E402,F401 - self-registering

SERVER_NAME = "libreoffice"
SERVER_VERSION = "0.9.6"
DEFAULT_PROTOCOL = "2024-11-05"

# TOOLS, TOOL_DEFS, _BASIC_TOOLS, _NO_UNDO, _full_tier, _advertised_tools all
# come in via the star-import above — core.__all__ already carries them under
# these exact names (the offline suite reaches for them here).




# Sent once, in the initialize reply. This is where a server says HOW it wants
# to be used — the standard place for it, and cheaper than repeating guidance in
# 186 tool descriptions. Spend it on decisions, not on trivia.
SERVER_INSTRUCTIONS = """\
You are editing documents that are open in LibreOffice on the user's own screen.
Edits appear immediately and there is no separate commit step, so treat the
document as the user's live work, not a draft you own.

START by calling document_lifecycle. It reads the document and tells you which
phase it is in, what is already done, and the next few concrete steps. Call it
again whenever you are unsure what to do next, or after finishing a stage.

The three phases are advisory, and EVERY tool works in every phase — if the user
asks to export while you are still writing, just export.

  SETUP     Agree what the document is and who it is for. Set the title and the
            language, and settle the look (writer_format_document, or
            calc_format_table) BEFORE writing content — restyling later is far
            more disruptive than getting it right first.

  AUTHORING Build the content in visible increments. Use real headings rather
            than bold text, because headings drive the navigation, the table of
            contents and the PDF outline. Show the user what changed and ask
            before moving to the next section.

  CLOSING   Record the metadata (author, subject, keywords, rights), give every
            image alt text, save, and ask what kind of export they want — a
            plain PDF, an accessible one (tagged/pdfua), or a fillable form
            (form_fields).

BE INTERACTIVE. Ask before big or destructive changes, and show intermediate
results rather than doing everything in one silent burst. When the user's intent
is ambiguous, ask a single specific question instead of guessing.

BEFORE ANY LARGE OR DESTRUCTIVE EDIT, call checkpoint_document. LibreOffice does
NOT record bulk cell-range writes for undo, so Ctrl+Z will not bring back data
that calc_write_range overwrote — a checkpoint is the only way back.

Only about a quarter of the tools are advertised by default. `dispatch` with
tool='list' is the authoritative catalog of everything this server can do; check
it before telling a user something is not possible.

If a call fails, read the structured error: `retryable` says whether to try
again, and `hint` says what to do instead. lo_health explains most "it stopped
working" situations."""



# Server-side prompts: the closest thing MCP has to a shipped skill. Claude
# Desktop surfaces these for the USER to pick, which is what makes the session
# interactive rather than the model guessing when to start a workflow.
PROMPTS = [
    {"name": "start_document",
     "description": "Begin a new document with Claude: agree the purpose, then "
                    "set the title, language and house style before writing.",
     "arguments": [
         {"name": "kind", "description": "'writer' or 'calc'", "required": False},
         {"name": "about", "description": "what the document is for", "required": False}],
     "text": "I want to start a new {kind} document about {about}.\n\n"
             "Call document_lifecycle first. Then, before writing any content,\n"
             "walk me through the setup phase one question at a time:\n"
             "  1. what this document is for and who will read it\n"
             "  2. a title, and the language it should be in\n"
             "  3. the look — page size, margins, base font, heading style\n"
             "Apply each choice as we agree it so I can see it, and do not start\n"
             "writing the body until I say the setup looks right."},
    {"name": "review_document",
     "description": "Check the open document for problems before it goes out: "
                    "broken formulas, missing alt text, incomplete metadata.",
     "arguments": [],
     "text": "Review the document that is open.\n\n"
             "Call document_lifecycle and lo_health, and for a spreadsheet also\n"
             "calc_detect_errors. Then tell me, as a short list:\n"
             "  - anything actually broken\n"
             "  - anything missing before this could be shared\n"
             "  - what you suggest doing about each\n"
             "Do not change anything yet — show me the list and let me choose."},
    {"name": "finish_document",
     "description": "Close out a document: metadata, accessibility, save, and "
                    "the right kind of export.",
     "arguments": [
         {"name": "purpose", "description": "e.g. 'email it', 'print it', 'a fillable form'",
          "required": False}],
     "text": "I am finished writing. Help me close this document out for: {purpose}\n\n"
             "Call document_lifecycle for the closing checklist, then take me\n"
             "through it one step at a time:\n"
             "  1. metadata — author, subject, keywords, and any licence\n"
             "  2. alt text for any image that lacks it\n"
             "  3. where to save it\n"
             "  4. the export — ask whether I need a plain PDF, an accessible\n"
             "     one (tagged/PDF-UA), or a fillable form, and explain the\n"
             "     difference briefly rather than choosing for me.\n"
             "Confirm each step with me before doing the next."},
]



def handle(message):
    method = message.get("method")
    mid = message.get("id")

    if method == "initialize":
        params = message.get("params") or {}
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return _result(mid, {
            "protocolVersion": version,
            "capabilities": {"tools": {}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })
    if method == "prompts/list":
        return _result(mid, {"prompts": [
            {"name": p["name"], "description": p["description"],
             "arguments": p.get("arguments", [])} for p in PROMPTS]})
    if method == "prompts/get":
        params = message.get("params") or {}
        wanted = params.get("name")
        prompt = next((p for p in PROMPTS if p["name"] == wanted), None)
        if prompt is None:
            return _error(mid, -32602, "Unknown prompt: %s" % wanted)
        body = prompt["text"]
        for key, value in (params.get("arguments") or {}).items():
            body = body.replace("{%s}" % key, str(value))
        return _result(mid, {
            "description": prompt["description"],
            "messages": [{"role": "user",
                          "content": {"type": "text", "text": body}}]})
    if method == "notifications/initialized":
        return None  # notification, no reply
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": _advertised_tools()})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        func = TOOLS.get(name)
        if func is None:
            return _error(mid, -32602, "Unknown tool: %s" % name)
        try:
            payload = _run_with_timeout(
                lambda: _call_with_reconnect(func, args, name), _call_timeout())
            # Two blocks: a human-readable narration first (so an operator
            # watching Claude's CLI/Desktop sees WHAT was done in the document),
            # then the structured JSON (the model chains on content[-1]).
            summary = _action_summary(name, args, payload)
            text = json.dumps(payload, ensure_ascii=False)
            return _result(mid, {"content": [
                {"type": "text", "text": summary},
                {"type": "text", "text": text},
            ]})
        except Exception as exc:  # tool errors are reported in-band, not as JSON-RPC errors
            # Two blocks, mirroring the success shape: a human-readable line
            # first, then the structured form the caller can branch on.
            # UNO exceptions often have an EMPTY str() — always name the type.
            info = _classify_error(exc)
            line = "Error [%s] %s: %s" % (info["code"], info["error_type"],
                                          info["message"])
            if info["hint"]:
                line += "\nHint: " + info["hint"]
            return _result(mid, {"content": [
                {"type": "text", "text": line},
                {"type": "text", "text": json.dumps(info, ensure_ascii=False)}],
                "isError": True})

    if mid is not None:
        return _error(mid, -32601, "Unknown method: %s" % method)
    return None  # unknown notification



def main():
    # Windows bundled Python defaults stdio to the locale codepage (cp1252),
    # which mangles Arabic/Unicode arguments on the way IN (bilingual sheet
    # names failed getByName). Force UTF-8 both ways.
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _log("LibreOffice MCP server ready (stdio, %d of %d tools advertised; "
         "LO_TOOLS=%s). LO_UNO_PORT=%s"
         % (len(_advertised_tools()), len(TOOLS),
            os.environ.get("LO_TOOLS", "basic"),
            os.environ.get("LO_UNO_PORT", "2002")))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _log("ignoring non-JSON line")
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()



if __name__ == "__main__":
    main()
