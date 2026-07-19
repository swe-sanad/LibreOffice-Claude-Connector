# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""AWT UI helpers for the connector: message boxes, an instruction prompt, a
settings dialog, and an off-UI-thread "progress while working" runner.

All of this needs the UNO runtime and a live frame, so it is only exercised
inside LibreOffice (not in the offline unit tests). Kept small and defensive.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, Tuple

import unohelper
from com.sun.star.awt import XCallback


# --------------------------------------------------------------------------- #
# Message boxes
# --------------------------------------------------------------------------- #

def _toolkit(ctx: Any) -> Any:
    return ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.Toolkit", ctx)


def message_box(ctx: Any, parent_win: Any, title: str, text: str,
                box_type: str = "infobox", buttons: int = 1) -> int:
    """Show a modal message box. ``box_type`` in {infobox,errorbox,warningbox,
    querybox}. Returns the pressed-button id (falls back to print if headless).
    """
    if parent_win is None:  # headless / no frame: don't crash
        print("[claude-connector] %s: %s" % (title, text))
        return 0
    from com.sun.star.awt.MessageBoxType import (
        INFOBOX, ERRORBOX, WARNINGBOX, QUERYBOX)
    mapping = {"infobox": INFOBOX, "errorbox": ERRORBOX,
               "warningbox": WARNINGBOX, "querybox": QUERYBOX}
    box = _toolkit(ctx).createMessageBox(
        parent_win, mapping.get(box_type, INFOBOX), buttons, title, text)
    result = box.execute()
    box.dispose()
    return result


def info_box(ctx, parent_win, text, title="Claude Connector"):
    return message_box(ctx, parent_win, title, text, "infobox", 1)


def error_box(ctx, parent_win, text, title="Claude Connector"):
    return message_box(ctx, parent_win, title, text, "errorbox", 1)


# --------------------------------------------------------------------------- #
# Dialog helpers
# --------------------------------------------------------------------------- #

def _new_dialog_model(ctx, width, height, title):
    model = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialogModel", ctx)
    model.Width = width
    model.Height = height
    model.Title = title
    return model


def _add(model, kind, name, **props):
    control = model.createInstance("com.sun.star.awt.UnoControl%sModel" % kind)
    for key, value in props.items():
        setattr(control, key, value)
    model.insertByName(name, control)
    return control


def _show_dialog(ctx, model, parent_win):
    dialog = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.UnoControlDialog", ctx)
    dialog.setModel(model)
    dialog.setVisible(False)
    dialog.createPeer(_toolkit(ctx), parent_win)
    return dialog


def prompt_instruction(ctx, parent_win, title, label,
                       default: str = "") -> Optional[str]:
    """Modal multi-line prompt. Returns the text, or ``None`` if cancelled."""
    model = _new_dialog_model(ctx, 240, 130, title)
    _add(model, "FixedText", "lbl", PositionX=8, PositionY=6, Width=224, Height=24,
         Label=label, MultiLine=True)
    _add(model, "Edit", "edit", PositionX=8, PositionY=34, Width=224, Height=70,
         MultiLine=True, VScroll=True, Text=default)
    _add(model, "Button", "ok", PositionX=128, PositionY=108, Width=50, Height=16,
         Label="Ask Claude", PushButtonType=1, DefaultButton=True)   # OK
    _add(model, "Button", "cancel", PositionX=182, PositionY=108, Width=50,
         Height=16, Label="Cancel", PushButtonType=2)                # CANCEL

    dialog = _show_dialog(ctx, model, parent_win)
    try:
        pressed = dialog.execute()
        text = dialog.getControl("edit").getModel().Text
    finally:
        dialog.dispose()
    return text if pressed == 1 else None


def settings_dialog(ctx, parent_win, cfg, has_key,
                    model_choices=("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"),
                    provider_choices=("anthropic", "openai_compatible")
                    ) -> Optional[Tuple[Optional[str], str, str, str]]:
    """Provider + model + endpoint + API-key settings. Returns
    ``(new_key_or_None, model, provider, endpoint)`` on Save, else ``None``.
    A blank key means "keep the existing key"."""
    current_model = cfg.get("model", "claude-sonnet-5")
    current_provider = cfg.get("provider", "anthropic")
    current_endpoint = cfg.get("base_url", "")
    key_hint = "•••• stored" if has_key else "(none set)"

    model = _new_dialog_model(ctx, 264, 188, "Claude Connector — Settings")
    _add(model, "FixedText", "l0", PositionX=8, PositionY=10, Width=88, Height=12,
         Label="Provider:")
    prov = _add(model, "ComboBox", "provider", PositionX=100, PositionY=8,
                Width=156, Height=14, Dropdown=True, Text=current_provider)
    prov.StringItemList = tuple(provider_choices)

    _add(model, "FixedText", "l1", PositionX=8, PositionY=32, Width=88, Height=12,
         Label="Model:")
    combo = _add(model, "ComboBox", "model", PositionX=100, PositionY=30,
                 Width=156, Height=14, Dropdown=True, Text=current_model)
    combo.StringItemList = tuple(model_choices)

    _add(model, "FixedText", "l2", PositionX=8, PositionY=56, Width=88, Height=12,
         Label="Endpoint (base URL):")
    _add(model, "Edit", "endpoint", PositionX=100, PositionY=54, Width=156,
         Height=14, Text=current_endpoint)

    _add(model, "FixedText", "l3", PositionX=8, PositionY=80, Width=88, Height=12,
         Label="API key:")
    _add(model, "FixedText", "hint", PositionX=100, PositionY=80, Width=156,
         Height=12, Label="current: %s" % key_hint)
    _add(model, "Edit", "key", PositionX=100, PositionY=94, Width=156, Height=14,
         EchoChar=ord("*"))
    _add(model, "FixedText", "l4", PositionX=8, PositionY=116, Width=248, Height=44,
         MultiLine=True,
         Label=("Leave the key blank to keep the stored one (encrypted per-user via "
                "Windows DPAPI; never in the config file). Local providers "
                "(Ollama/LM Studio) need no key — set the endpoint to e.g. "
                "http://localhost:11434/v1."))

    _add(model, "Button", "save", PositionX=150, PositionY=166, Width=50,
         Height=16, Label="Save", PushButtonType=1, DefaultButton=True)
    _add(model, "Button", "cancel", PositionX=206, PositionY=166, Width=50,
         Height=16, Label="Cancel", PushButtonType=2)

    dialog = _show_dialog(ctx, model, parent_win)
    try:
        pressed = dialog.execute()
        chosen_model = dialog.getControl("model").getModel().Text.strip()
        chosen_provider = dialog.getControl("provider").getModel().Text.strip()
        endpoint = dialog.getControl("endpoint").getModel().Text.strip()
        key_value = dialog.getControl("key").getModel().Text.strip()
    finally:
        dialog.dispose()
    if pressed != 1:
        return None
    return ((key_value or None), (chosen_model or current_model),
            (chosen_provider or current_provider), endpoint)


# --------------------------------------------------------------------------- #
# Off-UI-thread work with a modal progress dialog
# --------------------------------------------------------------------------- #

class _EndDialogCallback(unohelper.Base, XCallback):
    """Marshalled onto the main thread to end the progress dialog safely."""

    def __init__(self, dialog):
        self._dialog = dialog

    def notify(self, data):
        try:
            self._dialog.endExecute()
        except Exception:
            pass


# Returned when the user dismisses the progress dialog before the work finishes.
CANCELLED = object()


def run_with_progress(ctx, parent_win, title, message,
                      work: Callable[[], Any]) -> Any:
    """Run ``work()`` on a worker thread while a modal progress dialog keeps the
    UI responsive; return ``work()``'s result (or re-raise its exception).

    If the user dismisses the dialog (Escape / close button) before the work
    completes, returns the :data:`CANCELLED` sentinel — callers MUST check for it
    before using the result (otherwise a dismissal would feed ``None`` into the
    document write). The document read/write must happen on the caller's (main)
    thread — only the network call belongs inside ``work``.
    """
    if parent_win is None:  # headless: just run it synchronously
        return work()

    model = _new_dialog_model(ctx, 200, 46, title)
    _add(model, "FixedText", "msg", PositionX=12, PositionY=14, Width=176,
         Height=20, Label=message, Align=1)
    dialog = _show_dialog(ctx, model, parent_win)

    holder = {"result": None, "exc": None}
    done = threading.Event()
    async_cb = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.AsyncCallback", ctx)
    ender = _EndDialogCallback(dialog)

    def worker():
        try:
            holder["result"] = work()
        except BaseException as exc:          # noqa: BLE001 - propagated below
            holder["exc"] = exc
        finally:
            done.set()
            try:
                async_cb.addCallback(ender, None)   # end dialog on main thread
            except Exception:
                try:
                    dialog.endExecute()
                except Exception:
                    pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        dialog.execute()                       # nested loop; returns on endExecute
    finally:
        dialog.dispose()

    if not done.is_set():
        # Dialog closed by the user while the call was still running.
        return CANCELLED
    if holder["exc"] is not None:
        raise holder["exc"]
    return holder["result"]
