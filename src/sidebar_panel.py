# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A sidebar Deck + Panel for the Claude connector (the VSCode-style panel).

The panel hosts buttons that fire the SAME dispatch commands as the menu/toolbar
(``com.swepioneers.claudeconnector:Transform`` / ``:Settings``) via the
DispatchHelper, so it reuses the fully-tested action flow — no duplicated logic.

Wiring (all four pieces must agree, or the deck silently never appears):
  * ``Sidebar.xcu``  — declares the Deck + Panel; the Panel's ImplementationURL
    is ``private:resource/toolpanel/ClaudeSidebar/ClaudePanel``.
  * ``Factories.xcu``— maps the ``ClaudeSidebar`` token to this component's
    implementation name (``IMPL_NAME`` below).
  * this component   — a ``XUIElementFactory`` whose ``createUIElement`` returns
    an ``XUIElement`` → ``XToolPanel`` owning an AWT window.
  * ``manifest.xml`` — registers this file + both .xcu files.
"""

import traceback

import unohelper
from com.sun.star.ui import XUIElementFactory, XUIElement, XToolPanel
from com.sun.star.ui.UIElementType import TOOLPANEL
from com.sun.star.awt import XActionListener
from com.sun.star.awt.PosSize import POSSIZE

# MUST equal FactoryImplementation in Factories.xcu.
IMPL_NAME = "com.swepioneers.claudeconnector.SidebarFactory"

_TRANSFORM = "com.swepioneers.claudeconnector:Transform"
_SETTINGS = "com.swepioneers.claudeconnector:Settings"


class _DispatchButton(unohelper.Base, XActionListener):
    """Fires an existing dispatch command when its button is clicked."""

    def __init__(self, ctx, frame, command):
        self.ctx = ctx
        self.frame = frame
        self.command = command

    def actionPerformed(self, _event):
        try:
            helper = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.DispatchHelper", self.ctx)
            helper.executeDispatch(self.frame, self.command, "_self", 0, ())
        except Exception:
            traceback.print_exc()

    def disposing(self, _event):
        pass


class ClaudeToolPanel(unohelper.Base, XToolPanel):
    def __init__(self, window):
        self.Window = window        # the XWindow the sidebar displays

    def createAccessible(self, _parent):
        return self.Window

    def getAccessibleContext(self):
        return None


class ClaudeUIElement(unohelper.Base, XUIElement):
    def __init__(self, ctx, frame, parent_window, url):
        self.ctx = ctx
        self.frame = frame
        self.parent_window = parent_window
        self.ResourceURL = url
        self.Type = TOOLPANEL
        self.Frame = frame
        self._panel = None
        self._root = None
        self._listeners = []        # keep refs so they aren't GC'd

    def getRealInterface(self):
        if self._panel is None:
            self._root = self._build_window()
            self._panel = ClaudeToolPanel(self._root)
        return self._panel

    def _new(self, kind):
        return self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.%s" % kind, self.ctx)

    def _build_window(self):
        toolkit = self._new("Toolkit")
        container = self._new("UnoControlContainer")
        container.setModel(self._new("UnoControlContainerModel"))
        container.createPeer(toolkit, self.parent_window)
        size = self.parent_window.getPosSize()
        container.setPosSize(0, 0, size.Width, size.Height, POSSIZE)
        width = size.Width

        label = self._new("UnoControlFixedText")
        label_model = self._new("UnoControlFixedTextModel")
        label_model.Label = "Select cells (Calc) or text (Writer), then:"
        label_model.MultiLine = True
        label.setModel(label_model)
        container.addControl("lbl", label)
        label.setPosSize(6, 6, width - 12, 28, POSSIZE)

        self._add_button(container, "btnTransform",
                         "Transform Selection with Claude",
                         6, 40, width - 12, 30, _TRANSFORM)
        self._add_button(container, "btnSettings", "Settings…",
                         6, 76, width - 12, 24, _SETTINGS)

        container.setVisible(True)
        return container

    def _add_button(self, container, name, caption, x, y, w, h, command):
        button = self._new("UnoControlButton")
        model = self._new("UnoControlButtonModel")
        model.Label = caption
        button.setModel(model)
        container.addControl(name, button)
        button.setPosSize(x, y, w, h, POSSIZE)
        listener = _DispatchButton(self.ctx, self.frame, command)
        button.addActionListener(listener)
        self._listeners.append(listener)

    def postDisposing(self):
        if self._root is not None:
            try:
                self._root.dispose()
            except Exception:
                pass
            self._root = None
        self._panel = None
        self._listeners = []


class ClaudeSidebarFactory(unohelper.Base, XUIElementFactory):
    def __init__(self, ctx):
        self.ctx = ctx

    def createUIElement(self, url, args):
        frame = None
        parent = None
        for prop in args:
            if prop.Name == "Frame":
                frame = prop.Value
            elif prop.Name == "ParentWindow":
                parent = prop.Value
        if frame is None or parent is None:
            return None
        try:
            return ClaudeUIElement(self.ctx, frame, parent, url)
        except Exception:
            traceback.print_exc()
            return None


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ClaudeSidebarFactory, IMPL_NAME, (IMPL_NAME,))
