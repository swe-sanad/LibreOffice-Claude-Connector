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

Layout gotchas (the "blank panel" bugs):
  * The sidebar creates the panel while its parent window is still 0×0, so a
    one-shot layout at build time produces invisible (negative-width) controls.
    Controls are therefore (re)laid out from a window-resize listener.
  * The sidebar decides the panel's height by querying XSidebarPanel
    ``getHeightForWidth``; a panel that doesn't implement it gets zero height.
"""

import traceback

import uno
import unohelper
from com.sun.star.ui import (XUIElementFactory, XUIElement, XToolPanel,
                             XSidebarPanel)
from com.sun.star.ui.UIElementType import TOOLPANEL
from com.sun.star.awt import XActionListener, XWindowListener
from com.sun.star.awt.PosSize import POSSIZE

# MUST equal FactoryImplementation in Factories.xcu.
IMPL_NAME = "com.swepioneers.claudeconnector.SidebarFactory"

_TRANSFORM = "com.swepioneers.claudeconnector:Transform"
_SETTINGS = "com.swepioneers.claudeconnector:Settings"

_MARGIN = 6
_PANEL_HEIGHT = 110        # label (6..34) + transform (40..70) + settings (76..100)
_MIN_INNER_WIDTH = 60
_FALLBACK_WIDTH = 180      # if neither container nor parent has a size yet


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


class _RelayoutListener(unohelper.Base, XWindowListener):
    """Re-lays-out the panel whenever the sidebar resizes/shows it."""

    def __init__(self, element):
        self.element = element

    def windowResized(self, _event):
        if self.element is not None:
            self.element.layout()

    def windowShown(self, _event):
        if self.element is not None:
            self.element.layout()

    def windowMoved(self, _event):
        pass

    def windowHidden(self, _event):
        pass

    def disposing(self, _event):
        self.element = None


class ClaudeToolPanel(unohelper.Base, XToolPanel, XSidebarPanel):
    def __init__(self, window):
        self.Window = window        # the XWindow the sidebar displays

    def createAccessible(self, _parent):
        return self.Window

    def getAccessibleContext(self):
        return None

    # XSidebarPanel — the sidebar sizes the panel from this answer.
    def getHeightForWidth(self, _width):
        size = uno.createUnoStruct("com.sun.star.ui.LayoutSize")
        size.Minimum = _PANEL_HEIGHT
        size.Preferred = _PANEL_HEIGHT
        size.Maximum = _PANEL_HEIGHT
        return size

    def getMinimalWidth(self):
        return _MIN_INNER_WIDTH + 2 * _MARGIN


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
        self._resize_listener = None
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
        self._root = container

        label = self._new("UnoControlFixedText")
        label_model = self._new("UnoControlFixedTextModel")
        label_model.Label = "Select cells (Calc) or text (Writer), then:"
        label_model.MultiLine = True
        label.setModel(label_model)
        container.addControl("lbl", label)

        self._add_button(container, "btnTransform",
                         "Transform Selection with Claude", _TRANSFORM)
        self._add_button(container, "btnSettings", "Settings…", _SETTINGS)

        self._resize_listener = _RelayoutListener(self)
        self.parent_window.addWindowListener(self._resize_listener)
        container.addWindowListener(self._resize_listener)

        self.layout()
        container.setVisible(True)
        return container

    def layout(self):
        """Size the container to its parent and lay out the controls.

        Safe to call at any time; called again from the resize listener once
        the sidebar gives the parent its real size.
        """
        container = self._root
        if container is None:
            return
        try:
            psize = self.parent_window.getPosSize()
            csize = container.getPosSize()
            if psize.Width > 0 and (csize.Width != psize.Width or
                                    csize.Height != max(psize.Height,
                                                        _PANEL_HEIGHT)):
                container.setPosSize(0, 0, psize.Width,
                                     max(psize.Height, _PANEL_HEIGHT), POSSIZE)
                csize = container.getPosSize()

            width = csize.Width if csize.Width > 0 else psize.Width
            if width <= 0:
                width = _FALLBACK_WIDTH
            inner = max(width - 2 * _MARGIN, _MIN_INNER_WIDTH)

            container.getControl("lbl").setPosSize(
                _MARGIN, 6, inner, 28, POSSIZE)
            container.getControl("btnTransform").setPosSize(
                _MARGIN, 40, inner, 30, POSSIZE)
            container.getControl("btnSettings").setPosSize(
                _MARGIN, 76, inner, 24, POSSIZE)
        except Exception:
            traceback.print_exc()

    def _add_button(self, container, name, caption, command):
        button = self._new("UnoControlButton")
        model = self._new("UnoControlButtonModel")
        model.Label = caption
        button.setModel(model)
        container.addControl(name, button)
        listener = _DispatchButton(self.ctx, self.frame, command)
        button.addActionListener(listener)
        self._listeners.append(listener)

    def postDisposing(self):
        if self._resize_listener is not None:
            try:
                self.parent_window.removeWindowListener(self._resize_listener)
            except Exception:
                pass
            self._resize_listener = None
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
