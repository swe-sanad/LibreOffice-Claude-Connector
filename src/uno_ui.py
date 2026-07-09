# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
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
                    model_choices=("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8")
                    ) -> Optional[Tuple[Optional[str], str]]:
    """Model + API-key settings. Returns ``(new_key_or_None, model_str)`` on
    Save, else ``None``. A blank key field means "keep the existing key"."""
    current_model = cfg.get("model", "claude-sonnet-5")
    key_hint = "•••• stored" if has_key else "(none set)"

    model = _new_dialog_model(ctx, 260, 150, "Claude Connector — Settings")
    _add(model, "FixedText", "l1", PositionX=8, PositionY=8, Width=90, Height=12,
         Label="Model:")
    combo = _add(model, "ComboBox", "model", PositionX=100, PositionY=6,
                 Width=152, Height=14, Dropdown=True, Text=current_model)
    combo.StringItemList = tuple(model_choices)

    _add(model, "FixedText", "l2", PositionX=8, PositionY=32, Width=90, Height=12,
         Label="Anthropic API key:")
    _add(model, "FixedText", "hint", PositionX=100, PositionY=32, Width=152,
         Height=12, Label="current: %s" % key_hint)
    _add(model, "Edit", "key", PositionX=100, PositionY=48, Width=152, Height=14,
         EchoChar=ord("*"))
    _add(model, "FixedText", "l3", PositionX=8, PositionY=70, Width=244, Height=40,
         MultiLine=True,
         Label=("Leave the key blank to keep the stored one. The key is stored "
                "encrypted per-user (Windows DPAPI); it is never written to the "
                "config file."))

    _add(model, "Button", "save", PositionX=148, PositionY=128, Width=50,
         Height=16, Label="Save", PushButtonType=1, DefaultButton=True)
    _add(model, "Button", "cancel", PositionX=202, PositionY=128, Width=50,
         Height=16, Label="Cancel", PushButtonType=2)

    dialog = _show_dialog(ctx, model, parent_win)
    try:
        pressed = dialog.execute()
        chosen_model = dialog.getControl("model").getModel().Text.strip()
        key_value = dialog.getControl("key").getModel().Text.strip()
    finally:
        dialog.dispose()
    if pressed != 1:
        return None
    return (key_value or None), (chosen_model or current_model)


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


def run_with_progress(ctx, parent_win, title, message,
                      work: Callable[[], Any]) -> Any:
    """Run ``work()`` on a worker thread while a modal progress dialog keeps the
    UI responsive; return ``work()``'s result (or re-raise its exception).

    The document read/write must happen on the caller's (main) thread — only the
    network call belongs inside ``work``.
    """
    if parent_win is None:  # headless: just run it synchronously
        return work()

    model = _new_dialog_model(ctx, 200, 46, title)
    _add(model, "FixedText", "msg", PositionX=12, PositionY=14, Width=176,
         Height=20, Label=message, Align=1)
    dialog = _show_dialog(ctx, model, parent_win)

    holder = {"result": None, "exc": None}
    async_cb = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.AsyncCallback", ctx)
    ender = _EndDialogCallback(dialog)

    def worker():
        try:
            holder["result"] = work()
        except BaseException as exc:          # noqa: BLE001 - propagated below
            holder["exc"] = exc
        finally:
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
        thread.join(timeout=1.0)
        dialog.dispose()

    if holder["exc"] is not None:
        raise holder["exc"]
    return holder["result"]
