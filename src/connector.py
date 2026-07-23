# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
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
    from claudeconn.providers import OpenAICompatibleClient
except ImportError:                    # flat layout (dev / a plain scripts dir)
    import config, keystore, uno_bridge, calc_actions, writer_actions, uno_ui
    from claude_client import ClaudeClient, ClaudeError, ClaudeConfigError
    from providers import OpenAICompatibleClient


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
            elif command == "Summarize":
                self._do_summarize()
            elif command == "Translate":
                self._do_translate()
            elif command == "FixGrammar":
                self._do_fix_grammar()
            elif command == "GenerateFormula":
                self._do_generate_formula()
            elif command == "ExplainRange":
                self._do_explain_range()
            elif command == "Settings":
                self._do_settings()
        except uno_bridge.SelectionError as exc:
            uno_ui.error_box(self.ctx, self._win(), str(exc))
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
        cfg = config.load_config()
        key = keystore.get_api_key()
        if cfg.get("provider") == "openai_compatible":
            # Local servers (Ollama/LM Studio) need no key; cloud ones use it as a
            # bearer token. Either way, don't demand an Anthropic key.
            client = OpenAICompatibleClient(api_key=key or "", **config.client_kwargs(cfg))
            return client, cfg
        if not key:
            raise ClaudeConfigError(
                "No Anthropic API key is set. Open 'Claude > Settings...' and "
                "paste your API key (it is stored encrypted on this machine).")
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
        cell_range = uno_bridge.get_calc_selection_range(doc)  # may raise SelectionError
        if cell_range is None:
            uno_ui.error_box(self.ctx, win, "Select one or more cells first.")
            return
        count = uno_bridge.range_cell_count(cell_range)
        if count > calc_actions.MAX_CELLS:
            uno_ui.error_box(
                self.ctx, win,
                "Selection is too large (%d cells; limit is %d). Select a smaller "
                "range." % (count, calc_actions.MAX_CELLS))
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
        if new_grid is uno_ui.CANCELLED:
            return
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
                client, instruction,
                max_tokens=cfg.get("max_tokens") or 1024,
                temperature=cfg.get("temperature")))

        result = uno_ui.run_with_progress(
            self.ctx, win, "Claude", "Contacting Claude…", work)
        if result is uno_ui.CANCELLED:
            return
        mode, out = result
        if mode == "replace":
            uno_bridge.replace_writer_selection(doc, out)      # main thread
        else:
            uno_bridge.insert_writer_at_caret(doc, out)        # main thread

    # -- additional commands (canned-instruction convenience wrappers) --- #
    def _wrong_doc(self, msg="Open a Calc spreadsheet or a Writer document first."):
        uno_ui.error_box(self.ctx, self._win(), msg)

    def _writer_selection(self, need_msg):
        """Return (text, has_selection) or (None, None) after showing an error."""
        text, has_sel = uno_bridge.get_writer_selection(self._doc())
        if not (text and text.strip()):
            uno_ui.error_box(self.ctx, self._win(), need_msg)
            return None, None
        return text, has_sel

    def _calc_grid(self):
        """Return (cell_range, grid) or (None, None) after showing an error."""
        win = self._win()
        cell_range = uno_bridge.get_calc_selection_range(self._doc())
        if cell_range is None:
            uno_ui.error_box(self.ctx, win, "Select one or more cells first.")
            return None, None
        count = uno_bridge.range_cell_count(cell_range)
        if count > calc_actions.MAX_CELLS:
            uno_ui.error_box(self.ctx, win, "Selection is too large (%d cells; "
                             "limit is %d)." % (count, calc_actions.MAX_CELLS))
            return None, None
        return cell_range, uno_bridge.read_range_grid(cell_range)

    def _run(self, message, work):
        """Run ``work`` on the worker thread behind the progress dialog; returns
        its result, or the CANCELLED sentinel."""
        return uno_ui.run_with_progress(self.ctx, self._win(), "Claude", message, work)

    def _do_summarize(self):
        doc = self._doc()
        if uno_bridge.is_writer(doc):
            text, _ = self._writer_selection("Select the text to summarize first.")
            if text is None:
                return
            client, cfg = self._make_client()
            out = self._run("Summarizing…", lambda: writer_actions.summarize_text(
                client, text, max_tokens=cfg.get("max_tokens"),
                temperature=cfg.get("temperature")))
            if out is uno_ui.CANCELLED:
                return
            uno_bridge.insert_writer_at_caret(doc, "\n" + out)
        elif uno_bridge.is_calc(doc):
            _, grid = self._calc_grid()
            if grid is None:
                return
            client, cfg = self._make_client()
            out = self._run("Summarizing…", lambda: calc_actions.describe_grid(
                client, grid, mode="summarize", temperature=cfg.get("temperature")))
            if out is uno_ui.CANCELLED:
                return
            uno_ui.info_box(self.ctx, self._win(), out, "Summary")
        else:
            self._wrong_doc()

    def _do_translate(self):
        doc = self._doc()
        if not (uno_bridge.is_writer(doc) or uno_bridge.is_calc(doc)):
            self._wrong_doc()
            return
        language = uno_ui.prompt_instruction(
            self.ctx, self._win(), "Translate", "Translate to which language?")
        if not language:
            return
        if uno_bridge.is_writer(doc):
            text, _ = self._writer_selection("Select the text to translate first.")
            if text is None:
                return
            client, cfg = self._make_client()
            out = self._run("Translating…", lambda: writer_actions.translate_text(
                client, text, language, max_tokens=cfg.get("max_tokens"),
                temperature=cfg.get("temperature")))
            if out is uno_ui.CANCELLED:
                return
            uno_bridge.replace_writer_selection(doc, out)
        else:
            cell_range, grid = self._calc_grid()
            if grid is None:
                return
            client, cfg = self._make_client()
            new = self._run("Translating…", lambda: calc_actions.translate_range(
                client, grid, language, max_tokens=cfg.get("max_tokens"),
                temperature=cfg.get("temperature")))
            if new is uno_ui.CANCELLED:
                return
            uno_bridge.write_range_grid(cell_range, new)

    def _do_fix_grammar(self):
        doc = self._doc()
        if uno_bridge.is_writer(doc):
            text, _ = self._writer_selection("Select the text to correct first.")
            if text is None:
                return
            client, cfg = self._make_client()
            out = self._run("Fixing grammar…", lambda: writer_actions.fix_grammar_text(
                client, text, max_tokens=cfg.get("max_tokens"),
                temperature=cfg.get("temperature")))
            if out is uno_ui.CANCELLED:
                return
            uno_bridge.replace_writer_selection(doc, out)
        elif uno_bridge.is_calc(doc):
            cell_range, grid = self._calc_grid()
            if grid is None:
                return
            client, cfg = self._make_client()
            new = self._run("Fixing grammar…", lambda: calc_actions.fix_grammar_range(
                client, grid, max_tokens=cfg.get("max_tokens"),
                temperature=cfg.get("temperature")))
            if new is uno_ui.CANCELLED:
                return
            uno_bridge.write_range_grid(cell_range, new)
        else:
            self._wrong_doc()

    def _do_generate_formula(self):
        doc = self._doc()
        if not uno_bridge.is_calc(doc):
            self._wrong_doc("Generate Formula works in Calc — open a spreadsheet "
                            "and select the target cell.")
            return
        cell_range = uno_bridge.get_calc_selection_range(doc)
        if cell_range is None:
            uno_ui.error_box(self.ctx, self._win(),
                             "Select the cell where the formula should go.")
            return
        desc = uno_ui.prompt_instruction(
            self.ctx, self._win(), "Generate Formula",
            "Describe the formula you want:")
        if not desc:
            return
        try:
            sample = uno_bridge.read_range_grid(cell_range)
        except Exception:
            sample = None
        client, cfg = self._make_client()
        formula = self._run("Generating formula…", lambda: calc_actions.generate_formula(
            client, desc, sample=sample, temperature=cfg.get("temperature")))
        if formula is uno_ui.CANCELLED:
            return
        cell_range.getCellByPosition(0, 0).setFormula(formula)   # main thread

    def _do_explain_range(self):
        doc = self._doc()
        if not uno_bridge.is_calc(doc):
            self._wrong_doc("Explain Range works in Calc — open a spreadsheet and "
                            "select a range.")
            return
        _, grid = self._calc_grid()
        if grid is None:
            return
        client, cfg = self._make_client()
        out = self._run("Explaining…", lambda: calc_actions.describe_grid(
            client, grid, mode="explain", temperature=cfg.get("temperature")))
        if out is uno_ui.CANCELLED:
            return
        uno_ui.info_box(self.ctx, self._win(), out, "Explanation")

    def _do_settings(self):
        win = self._win()
        cfg = config.load_config()
        result = uno_ui.settings_dialog(
            self.ctx, win, cfg, keystore.has_stored_key())
        if result is None:
            return
        new_key, new_model, new_provider, endpoint = result
        cfg["model"] = new_model or cfg.get("model")
        cfg["provider"] = new_provider if new_provider in config.PROVIDERS else "anthropic"
        # If they switched to a local/OpenAI provider but left the Anthropic
        # endpoint, give them a working Ollama default instead of a dead URL.
        if cfg["provider"] == "openai_compatible" and (
                not endpoint or endpoint == config.DEFAULTS["base_url"]):
            endpoint = config.DEFAULT_OPENAI_BASE_URL
        cfg["base_url"] = endpoint or config.DEFAULTS["base_url"]
        config.save_config(cfg)
        if new_key:
            keystore.set_api_key(new_key)
        uno_ui.info_box(self.ctx, win, "Settings saved.")


# --- component registration -------------------------------------------------- #
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    ClaudeHandler, IMPL_NAME, (HANDLER_SERVICE,))
