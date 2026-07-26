# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Shared tools — recovery."""

from ..core import *      # noqa: F401,F403 - shared UNO machinery
from ..core import (_schema, _STR, _BOOL, _INT, _NUM, _RANGE, _SHEET,
                    _GRID)  # noqa: F401
from ..registry import register




# --------------------------------------------------------------------------- #
# Tools — status & selection
# --------------------------------------------------------------------------- #

def tool_lo_status(_args):
    _connect()
    advertised = len(_advertised_tools())
    out = {"connected": True,
           "transport": _state.get("transport"),
           "tools_advertised": advertised,
           "tools_total": len(TOOLS),
           "tool_tier": os.environ.get("LO_TOOLS", "basic").strip().lower(),
           "documents": [_doc_info(doc) for doc in _open_docs()]}
    if advertised < len(TOOLS):
        out["more_tools"] = ("%d further tools are available via dispatch "
                            "(use dispatch with tool='list' for the catalog); "
                            "set LO_TOOLS=full to advertise them all directly."
                            % (len(TOOLS) - advertised))
    if _state.get("transport") == "socket":
        tip = ("Connected over a socket, so Claude can only reach a LibreOffice "
               "it launched itself. Installing the agent-acceptor extension "
               "makes a LibreOffice you opened normally reachable too.")
        oxt = _bundled_oxt()
        if oxt:
            tip += (' It ships with this connector — install it once with:  '
                    'unopkg add --suppress-license -f "%s"  '
                    '(then restart LibreOffice).' % oxt)
        out["tip"] = tip
    try:      # WHICH office answered (crucial when a pipe reaches a stray one)
        ps = _state["smgr"].createInstanceWithContext(
            "com.sun.star.util.PathSettings", _state["ctx"])
        out["profile"] = ps.UserConfig
    except Exception:
        pass
    return out


def tool_lo_recover(args):
    """Report and act on LibreOffice's crash-recovery state."""
    action = str(args.get("action", "status")).lower()
    # Validate BEFORE connecting: a bad action or a missing confirmation is the
    # caller's mistake and should not depend on an office being reachable.
    if action not in ("status", "restore", "discard", "set_autosave"):
        raise RuntimeError("action must be one of status, restore, discard, "
                           "set_autosave.")
    if action == "discard" and not args.get("confirm"):
        raise RuntimeError(
            "Discarding recovery data permanently destroys the unsaved work "
            "LibreOffice saved during the crash. Pass confirm=true only after "
            "the user has explicitly agreed.")

    state = _recovery_state()

    if action == "status":
        state["advice"] = (
            "Documents are waiting to be recovered — call this tool with "
            "action='restore' to reopen them." if state["pending"] else
            "Nothing is pending recovery." if not state["crashed"] else
            "LibreOffice recorded a crash but has nothing left to recover.")
        if not state.get("autosave_enabled"):
            state["advice"] += (" AutoSave is OFF; action='set_autosave' with "
                                "minutes=10 turns it on for next time.")
        return state

    if action == "restore":
        if not state["pending"]:
            return dict(state, restored=False,
                        note="Nothing was pending recovery, so nothing was done.")
        st = _connect()
        recovery = st["smgr"].createInstanceWithContext(
            "com.sun.star.frame.AutoRecovery", st["ctx"])
        url = _uno_struct("com.sun.star.util.URL")
        url.Complete = "vnd.sun.star.autorecovery:/doAutoRestore"
        try:      # the URL must be parsed before a dispatcher will accept it
            parser = st["smgr"].createInstanceWithContext(
                "com.sun.star.util.URLTransformer", st["ctx"])
            _, url = parser.parseStrict(url)
        except Exception:
            pass
        recovery.dispatch(url, (_pv("SetAutoRecoveryState", True),))
        return {"restored": True, "documents": state["pending"],
                "note": "Recovered documents are now open — save them to a real "
                        "location before doing anything else."}

    if action == "discard":
        node = _config_node(_RECOVERY_PATH, writable=True)
        items = node.getByName("RecoveryList")
        removed = list(items.getElementNames())
        for name in removed:
            try:
                items.removeByName(name)
            except Exception:
                pass
        node.commitChanges()
        return {"discarded": removed, "count": len(removed)}

    # the only action left; the set is validated at the top
    minutes = int(args.get("minutes", 10))
    node = _config_node(_RECOVERY_PATH, writable=True)
    auto = node.getByName("AutoSave")
    auto.setPropertyValue("Enabled", minutes > 0)
    if minutes > 0:
        auto.setPropertyValue("TimeIntervall", minutes)
    node.commitChanges()
    return {"autosave_enabled": minutes > 0, "autosave_minutes": minutes}


def tool_checkpoint_document(args):
    """Snapshot a document to a side file so a risky edit can be rolled back.

    This is the rollback that undo cannot give: LibreOffice does not record bulk
    range writes for undo at all (docs/KNOWN-GAPS.md), so for anything that
    rewrites a range, a checkpoint is the ONLY way back.
    """
    import shutil
    import time

    action = str(args.get("action", "create")).lower()
    folder = _checkpoint_dir()

    if action == "list":
        rows = []
        for name in sorted(os.listdir(folder), reverse=True):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                rows.append({"id": name, "path": full,
                             "size_bytes": os.path.getsize(full),
                             "saved_at": time.strftime(
                                 "%Y-%m-%d %H:%M:%S",
                                 time.localtime(os.path.getmtime(full)))})
        return {"checkpoints": rows, "count": len(rows), "folder": folder}

    if action == "create":
        doc = _select_doc(args) or _current_doc()
        info = _doc_info(doc)
        original = doc.getURL() or ""
        # strip the title's own extension first, else "report.ods" sanitises to
        # "reportods" and the id reads as gibberish
        base = os.path.splitext(info.get("title") or "untitled")[0]
        stem = "".join(c for c in base
                       if c.isalnum() or c in "-_ ").strip() or "untitled"
        ext = os.path.splitext(original)[1] or ".ods"
        cid = "%s__%s%s" % (time.strftime("%Y%m%d-%H%M%S"), stem, ext)
        target = os.path.join(folder, cid)
        # storeToURL writes a COPY and leaves the live document's own location
        # and modified flag alone — storeAsURL would silently re-point it here.
        doc.storeToURL(_to_url(target), ())
        return {"checkpoint_id": cid, "path": target, "of": info,
                "original_url": original,
                "note": "Roll back with action='restore' and this checkpoint_id."}

    if action == "restore":
        cid = args.get("checkpoint_id")
        if not cid:
            raise RuntimeError("Give 'checkpoint_id' — action='list' shows them.")
        source = os.path.join(folder, os.path.basename(cid))
        if not os.path.isfile(source):
            raise RuntimeError("No checkpoint %r. Call action='list'." % cid)

        doc = _select_doc(args) or _current_doc()
        original = doc.getURL() or ""
        if not original:
            # never saved — there is nothing to restore OVER, so just open it
            opened = _desktop().loadComponentFromURL(
                _to_url(source), "_blank", 0, ())
            _activate(opened)
            return {"restored": "opened_alongside", "path": source,
                    "note": "The live document has never been saved, so the "
                            "checkpoint was opened as a separate document "
                            "instead of overwriting anything."}

        import unohelper
        target_path = unohelper.fileUrlToSystemPath(original)
        doc.setModified(False)      # discard in-memory edits, then close
        doc.close(False)
        shutil.copyfile(source, target_path)
        reopened = _desktop().loadComponentFromURL(_to_url(target_path),
                                                   "_blank", 0, ())
        _activate(reopened)
        return {"restored": target_path, "from_checkpoint": cid,
                "document": _doc_info(reopened)}

    raise RuntimeError("action must be one of create, list, restore.")


def tool_document_watch(args):
    """Detect that a document changed under us — including edits the USER made
    while Claude was thinking, which is the case worth guarding against before
    overwriting anything."""
    action = str(args.get("action", "check")).lower()
    if action not in ("start", "check", "stop", "list"):
        raise RuntimeError("action must be one of start, check, stop, list.")

    if action == "list":
        with _watch_lock():
            return {"watching": [
                {"document": key, "total_changes": e["total"],
                 "our_edits": e["ours"],
                 "user_edits": max(0, e["total"] - e["ours"])}
                for key, e in _WATCHERS.items()], "count": len(_WATCHERS)}

    doc = _select_doc(args) or _current_doc()
    key = _watch_key(doc)

    if action == "start":
        if key in _WATCHERS:                     # restart cleanly
            tool_document_watch({"action": "stop", "url": key})
        entry = {"total": 0, "ours": 0, "doc": doc}
        listener = _make_watcher()(entry)
        doc.addModifyListener(listener)
        entry["listener"] = listener
        with _watch_lock():
            _WATCHERS[key] = entry
        return {"watching": key, "document": _doc_info(doc),
                "note": "Call action='check' later to see whether the user "
                        "edited it in the meantime."}

    if action == "stop":
        entry = _WATCHERS.pop(key, None)
        if entry is None:
            return {"watching": None, "note": "That document was not watched."}
        try:
            entry["doc"].removeModifyListener(entry["listener"])
        except Exception:
            pass
        return {"stopped": key, "total_changes": entry["total"],
                "our_edits": entry["ours"],
                "user_edits": max(0, entry["total"] - entry["ours"])}

    entry = _WATCHERS.get(key)
    if entry is None:
        raise RuntimeError("Nothing is watching %r yet — call action='start' "
                           "before the step you want to guard." % key)
    with _watch_lock():
        total, ours = entry["total"], entry["ours"]
    user_edits = max(0, total - ours)
    return {"document": key, "total_changes": total, "our_edits": ours,
            "user_edits": user_edits,
            "changed_by_user": bool(user_edits),
            "advice": ("The user edited this document since the watch started — "
                       "re-read before overwriting." if user_edits else
                       "No edits from the user since the watch started.")}


def tool_lo_health(args):
    """Pre-flight: is it safe to start editing, and is anything at risk?"""
    state = _connect()
    report = {"connected": True, "transport": state.get("transport"),
              "call_timeout_seconds": _call_timeout(),
              "tools_advertised": len(_advertised_tools()),
              "tools_total": len(TOOLS)}
    problems = []

    docs = []
    for doc in _open_docs():
        info = _doc_info(doc)
        try:
            info["unsaved_changes"] = bool(doc.isModified())
        except Exception:
            info["unsaved_changes"] = None
        try:
            info["has_file"] = bool(doc.hasLocation())
        except Exception:
            info["has_file"] = None
        # a .~lock.<name># left behind by a crash blocks the next open
        url = doc.getURL() or ""
        if url:
            try:
                import unohelper
                path = unohelper.fileUrlToSystemPath(url)
                lock = os.path.join(os.path.dirname(path),
                                    ".~lock.%s#" % os.path.basename(path))
                info["lock_file"] = lock if os.path.exists(lock) else None
            except Exception:
                pass
        if info.get("unsaved_changes"):
            problems.append("%s has unsaved changes — checkpoint_document or "
                            "save_document before a risky edit."
                            % info.get("title"))
        docs.append(info)
    report["documents"] = docs

    recovery = _recovery_state()
    report["recovery"] = recovery
    if recovery.get("pending"):
        problems.append("%d document(s) are waiting to be recovered from a "
                        "crash — call lo_recover with action='restore'."
                        % len(recovery["pending"]))
    if recovery.get("autosave_enabled") is False:
        problems.append("AutoSave is off; lo_recover action='set_autosave' "
                        "minutes=10 reduces what a crash can cost.")
    if not docs:
        problems.append("No document is open — create_document, open_document "
                        "or list_recent_documents.")

    report["problems"] = problems
    report["healthy"] = not problems
    return report


TOOL_DEFS = [
    # --- status & selection ---
    {"name": "lo_status",
     "description": "Check the LibreOffice connection (reports the transport: pipe = agent-acceptor extension, socket = accept flag/auto-launch) and list open documents.",
     "inputSchema": _schema()},
    # --- failure recovery ---
    {"name": "lo_health",
     "description": "Pre-flight check before a risky edit: connection and transport, the call timeout, every open document with whether it has UNSAVED changes and a real file, stale .~lock files left by a crash, pending crash-recovery, and whether AutoSave is on. Returns a 'problems' list and a 'healthy' flag. Call this when something has gone wrong, or before a large/destructive change.",
     "inputSchema": _schema()},
    {"name": "lo_recover",
     "description": "LibreOffice's crash recovery, driven over UNO instead of the startup dialog. action 'status' (default) reports whether it crashed, what is waiting to be recovered and the AutoSave setting; 'restore' reopens the pending documents; 'discard' permanently destroys that unsaved work and needs confirm=true; 'set_autosave' turns AutoSave on with 'minutes' (0 = off).",
     "inputSchema": _schema({"action": dict(_STR, enum=["status", "restore", "discard", "set_autosave"]),
                             "minutes": dict(_INT, description="set_autosave: interval in minutes, 0 to disable"),
                             "confirm": dict(_BOOL, description="discard: required, destroys unsaved work from the crash")})},
    {"name": "document_watch",
     "description": "Notice when a document changes underneath you — in particular when the USER edits it while Claude is thinking. action 'start' begins watching, 'check' reports how many changes happened and separates OUR edits from the user's, 'stop' ends it, 'list' shows what is watched. Start a watch before a long or multi-step operation, then check before overwriting anything.",
     "inputSchema": _schema({"action": dict(_STR, enum=["start", "check", "stop", "list"]),
                             "title": dict(_STR, description="match the document by window-title substring"),
                             "url": dict(_STR, description="match the document by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
    {"name": "checkpoint_document",
     "description": "Snapshot a document to a side file so a risky edit can be undone. THIS IS THE ONLY ROLLBACK for anything that writes a cell range — LibreOffice does not record bulk range writes for undo, so Ctrl+Z cannot bring those back. action 'create' (default) saves a copy and returns a checkpoint_id, 'list' shows saved checkpoints, 'restore' puts one back (closing and reopening the document; unsaved edits since the checkpoint are lost).",
     "inputSchema": _schema({"action": dict(_STR, enum=["create", "list", "restore"]),
                             "checkpoint_id": dict(_STR, description="restore: the id from create/list"),
                             "title": dict(_STR, description="match the document by window-title substring"),
                             "url": dict(_STR, description="match the document by file URL/path substring"),
                             "index": dict(_INT, description="0-based index over open documents")})},
]

register(globals(), TOOL_DEFS,
         basic=['checkpoint_document', 'document_watch', 'lo_health', 'lo_recover', 'lo_status'],
         read_only=['checkpoint_document', 'document_watch', 'lo_health', 'lo_recover', 'lo_status'])
