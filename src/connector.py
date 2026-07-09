# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The registered UNO component — a ProtocolHandler that routes the extension's
menu / toolbar / shortcut commands to the Calc and Writer actions.

Command URLs (see Addons.xcu / ProtocolHandler.xcu):
    com.swepioneers.claudeconnector:Transform   -> rewrite/transform the selection
    com.swepioneers.claudeconnector:Settings     -> configure model + API key

This file is the ONLY component registered in the manifest. Its helper modules
live in ``pythonpath/claudeconn/`` inside the .oxt; we add that folder to
sys.path so the single unique top-level name we introduce is ``claudeconn``.
"""

import os
import sys

import uno
import unohelper
from com.sun.star.frame import XDispatchProvider, XDispatch
from com.sun.star.lang import XInitialization, XServiceInfo

# --- make the helper package importable, both packaged and flat (dev) -------- #
_HERE = os.path.dirname(os.path.realpath(__file__))
for _p in (os.path.join(_HERE, "pythonpath"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:                                   # packaged layout: pythonpath/claudeconn/
    from claudeconn import (config, keystore, uno_bridge,
                            calc_actions, writer_actions, uno_ui)
    from claudeconn.claude_client import ClaudeClient, ClaudeError, ClaudeConfigError
except ImportError:                    # flat layout (dev / a plain scripts dir)
    import config, keystore, uno_bridge, calc_actions, writer_actions, uno_ui
    from claude_client import ClaudeClient, ClaudeError, ClaudeConfigError


IMPL_NAME = "com.swepioneers.claudeconnector.Handler"
PROTOCOL = "com.swepioneers.claudeconnector"
HANDLER_SERVICE = "com.sun.star.frame.ProtocolHandler"


class ClaudeHandler(unohelper.Base, XDispatchProvider, XDispatch,
                    XInitialization, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx
        self.frame = None

    # -- XInitialization ------------------------------------------------- #
    def initialize(self, args):
        if args:
            self.frame = args[0]

    # -- XServiceInfo ---------------------------------------------------- #
    def getImplementationName(self):
        return IMPL_NAME

    def supportsService(self, name):
        return name == HANDLER_SERVICE

    def getSupportedServiceNames(self):
        return (HANDLER_SERVICE,)

    # -- XDispatchProvider ----------------------------------------------- #
    def queryDispatch(self, url, target_frame_name, search_flags):
        if url.Protocol == PROTOCOL + ":":
            return self
        return None

    def queryDispatches(self, requests):
        return tuple(self.queryDispatch(r.FeatureURL, r.FrameName, r.SearchFlags)
                     for r in requests)

    # -- XDispatch ------------------------------------------------------- #
    def dispatch(self, url, args):
        command = self._command(url)
        try:
            if command == "Transform":
                self._do_transform()
            elif command == "Settings":
                self._do_settings()
        except ClaudeConfigError as exc:
            uno_ui.error_box(self.ctx, self._win(), str(exc))
        except ClaudeError as exc:
            uno_ui.error_box(self.ctx, self._win(), str(exc))
        except Exception as exc:  # noqa: BLE001 - never let an exception escape UNO
            uno_ui.error_box(self.ctx, self._win(),
                             "Unexpected error: %s" % exc)

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass

    # -- helpers --------------------------------------------------------- #
    def _command(self, url):
        if getattr(url, "Path", ""):
            return url.Path
        complete = getattr(url, "Complete", "") or ""
        prefix = PROTOCOL + ":"
        return complete[len(prefix):] if complete.startswith(prefix) else ""

    def _win(self):
        return self.frame.getContainerWindow() if self.frame else None

    def _doc(self):
        return self.frame.getController().getModel() if self.frame else None

    def _make_client(self):
        key = keystore.get_api_key()
        if not key:
            raise ClaudeConfigError(
                "No Anthropic API key is set. Open 'Claude > Settings...' and "
                "paste your API key (it is stored encrypted on this machine).")
        cfg = config.load_config()
        client = ClaudeClient(api_key=key, **config.client_kwargs(cfg))
        return client, cfg

    # -- commands -------------------------------------------------------- #
    def _do_transform(self):
        doc = self._doc()
        if uno_bridge.is_calc(doc):
            self._transform_calc(doc)
        elif uno_bridge.is_writer(doc):
            self._transform_writer(doc)
        else:
            uno_ui.error_box(self.ctx, self._win(),
                             "Open a Calc spreadsheet or a Writer document first.")

    def _transform_calc(self, doc):
        win = self._win()
        cell_range = uno_bridge.get_calc_selection_range(doc)
        if cell_range is None:
            uno_ui.error_box(self.ctx, win, "Select one or more cells first.")
            return
        instruction = uno_ui.prompt_instruction(
            self.ctx, win, "Ask Claude (Calc)",
            "How should Claude transform the selected cells?")
        if not instruction:
            return
        grid = uno_bridge.read_range_grid(cell_range)          # main thread
        client, cfg = self._make_client()

        def work():                                            # worker thread
            return calc_actions.transform_range(
                client, grid, instruction,
                max_tokens=cfg.get("max_tokens"),
                temperature=cfg.get("temperature"))

        new_grid = uno_ui.run_with_progress(
            self.ctx, win, "Claude", "Contacting Claude…", work)
        uno_bridge.write_range_grid(cell_range, new_grid)      # main thread

    def _transform_writer(self, doc):
        win = self._win()
        text, has_selection = uno_bridge.get_writer_selection(doc)
        label = ("How should Claude rewrite the selected text?" if has_selection
                 else "What should Claude write at the cursor?")
        instruction = uno_ui.prompt_instruction(
            self.ctx, win, "Ask Claude (Writer)", label)
        if not instruction:
            return
        client, cfg = self._make_client()

        def work():                                            # worker thread
            if has_selection:
                return ("replace", writer_actions.rewrite_text(
                    client, text, instruction,
                    max_tokens=cfg.get("max_tokens"),
                    temperature=cfg.get("temperature")))
            return ("insert", writer_actions.generate_text(
                client, instruction, temperature=cfg.get("temperature")))

        mode, out = uno_ui.run_with_progress(
            self.ctx, win, "Claude", "Contacting Claude…", work)
        if mode == "replace":
            uno_bridge.replace_writer_selection(doc, out)      # main thread
        else:
            uno_bridge.insert_writer_at_caret(doc, out)        # main thread

    def _do_settings(self):
        win = self._win()
        cfg = config.load_config()
        result = uno_ui.settings_dialog(
            self.ctx, win, cfg, keystore.has_stored_key())
        if result is None:
            return
        new_key, new_model = result
        cfg["model"] = new_model or cfg.get("model")
        config.save_config(cfg)
        if new_key:
            keystore.set_api_key(new_key)
        uno_ui.info_box(self.ctx, win, "Settings saved.")


# --- component registration -------------------------------------------------- #
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ClaudeHandler, IMPL_NAME, (HANDLER_SERVICE,))
