# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Regression tests for the everyday-user surface: the advertised tool tier,
undo grouping, and the composite tools.

Offline — imports the MCP server module directly (uno is imported lazily, so no
LibreOffice is needed) and never opens a document.
"""

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "mcp"))
import libreoffice_mcp as m  # noqa: E402


class ToolRegistryTest(unittest.TestCase):
    """TOOLS and TOOL_DEFS drifting apart is this file's classic bug: a handler
    with no schema is invisible, a schema with no handler is a runtime error."""

    def test_every_def_has_a_handler_and_vice_versa(self):
        defs = {d["name"] for d in m.TOOL_DEFS}
        self.assertEqual(defs - set(m.TOOLS), set(), "schema without a handler")
        self.assertEqual(set(m.TOOLS) - defs, set(), "handler without a schema")

    def test_composites_are_registered(self):
        for name in ("calc_overview", "calc_format_table", "calc_clean_data",
                     "writer_format_document"):
            self.assertIn(name, m.TOOLS)
            self.assertIn(name, {d["name"] for d in m.TOOL_DEFS})

    def test_borrowed_everyday_tools_are_registered_and_advertised(self):
        # mined from the sibling projects; they exist to be found by a casual
        # user, so being in TOOLS but not the everyday tier defeats the point
        for name in ("calc_import_csv", "calc_detect_errors",
                     "list_recent_documents", "print_document",
                     "writer_resolve_comment", "writer_captions",
                     "writer_insert_caption"):
            self.assertIn(name, m.TOOLS)
            self.assertIn(name, {d["name"] for d in m.TOOL_DEFS})
            self.assertIn(name, m._BASIC_TOOLS)


class CalcErrorTableTest(unittest.TestCase):
    def test_covers_the_errors_users_actually_hit(self):
        # the four a student sees on a broken sheet
        for code, marker in ((532, "#DIV/0!"), (524, "#REF!"),
                             (525, "#NAME?"), (519, "#VALUE!")):
            self.assertIn(marker, m._CALC_ERRORS[code])

    def test_every_entry_names_a_marker(self):
        for code, text in m._CALC_ERRORS.items():
            self.assertTrue(text.startswith("#"),
                            "code %s should start with the cell marker" % code)


class ToolTierTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("LO_TOOLS", None)

    def tearDown(self):
        os.environ.pop("LO_TOOLS", None)
        if self._saved is not None:
            os.environ["LO_TOOLS"] = self._saved

    def test_default_tier_is_the_basic_subset(self):
        names = {d["name"] for d in m._advertised_tools()}
        self.assertEqual(names, set(m._BASIC_TOOLS))
        self.assertLess(len(names), len(m.TOOL_DEFS))

    def test_basic_tier_names_all_exist(self):
        self.assertEqual(m._BASIC_TOOLS - set(m.TOOLS), set())

    def test_dispatch_is_always_advertised(self):
        # the escape hatch to the unadvertised tools must never be hidden itself
        self.assertIn("dispatch", {d["name"] for d in m._advertised_tools()})

    def test_full_tier_advertises_everything(self):
        for value in ("full", "all", "true", "1", "YES"):
            os.environ["LO_TOOLS"] = value
            self.assertEqual(len(m._advertised_tools()), len(m.TOOL_DEFS),
                             "LO_TOOLS=%r should mean the full surface" % value)

    def test_unknown_value_falls_back_to_basic_not_empty(self):
        os.environ["LO_TOOLS"] = "flul"   # typo in a GUI config field
        self.assertEqual(len(m._advertised_tools()), len(m._BASIC_TOOLS))


class UndoGroupingTest(unittest.TestCase):
    def test_no_undo_names_all_exist(self):
        # a typo here silently undo-wraps a read instead of exempting it
        self.assertEqual(m._NO_UNDO - set(m.TOOLS), set())

    def test_mutating_tools_are_not_exempt(self):
        for name in ("calc_write_range", "calc_delete_rows", "calc_format_table",
                     "calc_clean_data", "writer_append_text",
                     "writer_format_document", "writer_find_replace",
                     "calc_import_csv", "writer_resolve_comment",
                     "writer_insert_caption", "writer_captions"):
            self.assertNotIn(name, m._NO_UNDO,
                             "%s mutates — it must get an undo context" % name)

    def test_read_only_tools_are_exempt(self):
        for name in ("calc_read_range", "calc_overview", "lo_status",
                     "writer_get_text", "list_documents", "calc_detect_errors",
                     "list_recent_documents", "print_document"):
            self.assertIn(name, m._NO_UNDO)

    def test_exempt_tool_never_opens_a_context(self):
        # cheap and office-independent: an exempt name short-circuits before
        # any UNO call, so this must be None whether or not an office is up
        self.assertIsNone(m._enter_undo("lo_status"))
        self.assertIsNone(m._enter_undo(None))

    def test_undo_helpers_are_best_effort(self):
        # no office, no open document, or no undo manager must all degrade to
        # "no grouping" rather than failing the tool call
        m._leave_undo(None)
        m._leave_undo(object())          # not an undo manager — swallowed
        try:
            m._enter_undo("calc_write_range")
        except Exception as exc:         # pragma: no cover - the thing we forbid
            self.fail("_enter_undo must never raise, got %r" % exc)

    def test_call_with_reconnect_still_returns_the_payload(self):
        seen = {}

        def fake(a):
            seen["args"] = a
            return "ok"

        self.assertEqual(m._call_with_reconnect(fake, {"x": 1}, "lo_status"), "ok")
        self.assertEqual(seen["args"], {"x": 1})

    def test_call_with_reconnect_propagates_non_connection_errors(self):
        def boom(_a):
            raise ValueError("bad range")

        with self.assertRaises(ValueError):
            m._call_with_reconnect(boom, {}, "lo_status")


class CallTimeoutTest(unittest.TestCase):
    """A wedged UNO call must become an error, not a dead server."""

    def setUp(self):
        # these tests poke the shared connection cache; a leak here makes a
        # LATER test connect for real and auto-launch an office
        self._saved = dict(m._state)

    def tearDown(self):
        m._state.clear()
        m._state.update(self._saved)

    def test_returns_the_value_when_fast_enough(self):
        self.assertEqual(m._run_with_timeout(lambda: "done", 5), "done")

    def test_zero_disables_the_timeout(self):
        self.assertEqual(m._run_with_timeout(lambda: "done", 0), "done")

    def test_slow_call_raises_tool_timeout(self):
        import time
        with self.assertRaises(m.ToolTimeout):
            m._run_with_timeout(lambda: time.sleep(3), 0.2)

    def test_timeout_drops_the_cached_bridge(self):
        # else the next call queues behind the stuck one and hangs too
        import time
        m._state["desktop"] = object()
        try:
            m._run_with_timeout(lambda: time.sleep(3), 0.2)
        except m.ToolTimeout:
            pass
        self.assertIsNone(m._state["desktop"])

    def test_exceptions_cross_the_thread_boundary(self):
        def boom():
            raise ValueError("inner")
        with self.assertRaises(ValueError):
            m._run_with_timeout(boom, 5)

    def test_env_override_and_bad_value(self):
        for raw, expected in (("30", 30.0), ("0", 0.0), ("nonsense", 120.0)):
            os.environ["LO_CALL_TIMEOUT"] = raw
            try:
                self.assertEqual(m._call_timeout(), expected)
            finally:
                os.environ.pop("LO_CALL_TIMEOUT", None)


class ErrorClassifierTest(unittest.TestCase):
    """The point of the codes is deciding retry vs ask-the-user vs give up."""

    def test_transient_failures_are_retryable(self):
        for exc in (m.ToolTimeout("no answer"),
                    RuntimeError("Binary URP bridge already disposed")):
            info = m._classify_error(exc)
            self.assertTrue(info["retryable"], info)

    def test_user_errors_are_not_retryable(self):
        cases = {
            "No document is currently open/active in LibreOffice.": "no_document",
            "The active document is not a Calc spreadsheet.": "wrong_doc_type",
            "The active document is not a Writer document.": "wrong_doc_type",
            "No sheet matches 'Q3'. Sheets: a ; b": "not_found",
            "No table named 'T9'.": "not_found",
            "3 documents are open but none is focused; focus one.": "ambiguous_document",
            "preset must be one of ['clean']": "invalid_argument",
        }
        for text, code in cases.items():
            info = m._classify_error(RuntimeError(text))
            self.assertEqual(info["code"], code, text)
            self.assertFalse(info["retryable"], text)

    def test_missing_argument_is_named(self):
        info = m._classify_error(KeyError("range"))
        self.assertEqual(info["code"], "invalid_argument")
        self.assertIn("range", info["hint"])

    def test_every_classification_carries_the_full_shape(self):
        for exc in (RuntimeError(""), KeyError("x"), m.ToolTimeout("t"),
                    RuntimeError("something nobody predicted")):
            info = m._classify_error(exc)
            self.assertEqual(set(info),
                             {"code", "error_type", "retryable", "message", "hint"})
            self.assertTrue(info["message"], "message must never be empty")

    def test_unrecognised_errors_fall_back_without_a_bogus_hint(self):
        info = m._classify_error(RuntimeError("something nobody predicted"))
        self.assertEqual(info["code"], "uno_error")
        self.assertEqual(info["hint"], "")


class RecoveryToolsTest(unittest.TestCase):
    def test_registered_and_advertised(self):
        for name in ("lo_health", "lo_recover", "checkpoint_document"):
            self.assertIn(name, m.TOOLS)
            self.assertIn(name, m._BASIC_TOOLS)
            self.assertIn(name, m._NO_UNDO,
                          "%s does not edit document content" % name)

    def test_discard_refuses_without_confirmation(self):
        # destroying unsaved crash data must never happen on a bare call
        with self.assertRaises(Exception) as caught:
            m.TOOLS["lo_recover"]({"action": "discard"})
        self.assertIn("confirm", str(caught.exception).lower())

    def test_unknown_actions_are_rejected(self):
        for tool, action in (("lo_recover", "nuke"),
                             ("checkpoint_document", "sideways")):
            with self.assertRaises(Exception):
                m.TOOLS[tool]({"action": action})


class LocaleNumberFormatTest(unittest.TestCase):
    def test_parses_bcp47_tags(self):
        for tag, lang, country in (("ar-LY", "ar", "LY"), ("en_US", "en", "US"),
                                   ("de", "de", "")):
            loc = m._parse_locale(tag)
            self.assertEqual((loc.Language, loc.Country), (lang, country), tag)

    def test_empty_tag_means_the_document_locale(self):
        loc = m._parse_locale(None)
        self.assertEqual((loc.Language, loc.Country), ("", ""))

    def test_constants_match_the_uno_api(self):
        # these are fixed by com.sun.star.util.NumberFormat; a wrong value here
        # silently formats money as a date
        self.assertEqual(m._NUMBER_TYPES["currency"], 8)
        self.assertEqual(m._NUMBER_TYPES["percent"], 128)
        self.assertEqual(m._NUMBER_TYPES["date"], 2)
        self.assertEqual(m._NUMBER_TYPES["number"], 16)


class DocumentWatchTest(unittest.TestCase):
    def tearDown(self):
        m._WATCHERS.clear()

    def test_registered_and_advertised(self):
        self.assertIn("document_watch", m.TOOLS)
        self.assertIn("document_watch", m._BASIC_TOOLS)
        self.assertIn("document_watch", m._NO_UNDO)

    def test_our_edits_are_counted_separately(self):
        entry = {"total": 0, "ours": 0, "doc": None}
        m._WATCHERS["doc"] = entry
        m._note_our_edit()
        m._note_our_edit()
        entry["total"] += 3          # as if UNO reported three modifications
        self.assertEqual(entry["ours"], 2)
        self.assertEqual(max(0, entry["total"] - entry["ours"]), 1)

    def test_note_our_edit_is_a_noop_with_no_watchers(self):
        m._note_our_edit()           # must not raise
        self.assertEqual(m._WATCHERS, {})

    def test_list_needs_no_document(self):
        self.assertEqual(m.TOOLS["document_watch"]({"action": "list"})["count"], 0)

    def test_unknown_action_rejected_before_connecting(self):
        with self.assertRaises(Exception) as caught:
            m.TOOLS["document_watch"]({"action": "sideways"})
        self.assertIn("action must be", str(caught.exception))
class MetadataPrintFormsTest(unittest.TestCase):
    """Document attributes, print setup, and the Form-menu surface."""

    def test_new_tools_registered_and_advertised(self):
        for name in ("print_settings", "set_alt_text", "writer_content_control"):
            self.assertIn(name, m.TOOLS)
            self.assertIn(name, {d["name"] for d in m.TOOL_DEFS})
            self.assertIn(name, m._BASIC_TOOLS)

    def test_content_edits_get_undo_but_settings_do_not(self):
        for name in ("set_alt_text", "writer_content_control"):
            self.assertNotIn(name, m._NO_UNDO, "%s edits the document" % name)
        self.assertIn("print_settings", m._NO_UNDO)

    def test_form_menu_is_covered(self):
        # the controls a real fillable form needs, beyond the original five
        for kind in ("radio", "combobox", "date", "time", "numeric", "currency",
                     "pattern", "formatted", "groupbox", "file", "imagebutton"):
            self.assertIn(kind, m._FORM_COMPONENTS)
        self.assertGreaterEqual(len(m._FORM_COMPONENTS), 19)

    def test_imagebutton_is_not_treated_as_labelled(self):
        # it has no Label property at all — setting one raises AttributeError,
        # which is how this shipped broken the first time
        self.assertNotIn("imagebutton", m._FORM_LABELLED)

    def test_form_kinds_advertised_match_the_implementation(self):
        by_name = {d["name"]: d for d in m.TOOL_DEFS}
        enum = by_name["insert_form_control"]["inputSchema"]["properties"]["kind"]["enum"]
        self.assertEqual(sorted(enum), sorted(m._FORM_COMPONENTS))

    def test_print_option_lists_are_per_application(self):
        # offering a Writer switch on a Calc doc just raises at the office
        self.assertIn("PrintProspectRTL", m._PRINT_SETTINGS_WRITER)
        self.assertIn("PrintFormulas", m._PRINT_SETTINGS_CALC)
        # 0.9.6 believed PrintEmptyPages was common to both; it is a Writer
        # document-setting only, and Calc's switches live on the page style
        self.assertNotIn("PrintEmptyPages", m._PRINT_SETTINGS_CALC)

    def test_print_settings_rejects_a_foreign_option(self):
        by_name = {d["name"]: d for d in m.TOOL_DEFS}
        self.assertIn("options", by_name["print_settings"]["inputSchema"]["properties"])

    def test_content_control_kinds_match_the_schema(self):
        by_name = {d["name"]: d for d in m.TOOL_DEFS}
        enum = by_name["writer_content_control"]["inputSchema"]["properties"]["kind"]["enum"]
        self.assertEqual(sorted(enum), sorted(m._CONTENT_CONTROL_KINDS))

    def test_pdf_accessibility_and_form_options_are_advertised(self):
        by_name = {d["name"]: d for d in m.TOOL_DEFS}
        props = by_name["export_document"]["inputSchema"]["properties"]
        for opt in ("tagged", "pdfua", "form_fields", "forms_type",
                    "owner_password", "can_print", "can_modify", "can_copy"):
            self.assertIn(opt, props, opt)

    def test_dublin_core_fields_are_advertised_with_the_right_shapes(self):
        by_name = {d["name"]: d for d in m.TOOL_DEFS}
        props = by_name["set_document_properties"]["inputSchema"]["properties"]
        # these three are sequence<string> in ODF; a bare string raises
        # CannotConvertException, so they must be advertised as arrays
        for multi in ("keywords", "contributor", "publisher", "relation"):
            self.assertEqual(props[multi].get("type"), "array", multi)
        for single in ("coverage", "identifier", "rights", "source", "type",
                       "language"):
            self.assertEqual(props[single].get("type"), "string", single)


class LifecycleTest(unittest.TestCase):
    """The guided session: instructions, prompts, and the phase tool."""

    def test_lifecycle_tool_registered_and_advertised(self):
        self.assertIn("document_lifecycle", m.TOOLS)
        self.assertIn("document_lifecycle", m._BASIC_TOOLS)
        self.assertIn("document_lifecycle", m._NO_UNDO)   # it only reads

    def test_initialize_carries_instructions_and_prompts(self):
        result = m.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                           "params": {}})["result"]
        self.assertIn("instructions", result)
        self.assertIn("prompts", result["capabilities"])
        text = result["instructions"]
        # the load-bearing claims: where to start, and the undo trap
        self.assertIn("document_lifecycle", text)
        self.assertIn("checkpoint_document", text)
        self.assertIn("dispatch", text)

    def test_instructions_name_all_three_phases(self):
        for phase in ("SETUP", "AUTHORING", "CLOSING"):
            self.assertIn(phase, m.SERVER_INSTRUCTIONS)

    def test_prompts_list_and_get(self):
        listed = m.handle({"jsonrpc": "2.0", "id": 1,
                           "method": "prompts/list"})["result"]["prompts"]
        self.assertEqual({p["name"] for p in listed},
                         {"start_document", "review_document", "finish_document"})
        for p in listed:
            self.assertTrue(p["description"])

    def test_prompt_arguments_are_substituted(self):
        got = m.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                        "params": {"name": "start_document",
                                   "arguments": {"kind": "calc",
                                                 "about": "a budget"}}})["result"]
        text = got["messages"][0]["content"]["text"]
        self.assertIn("calc", text)
        self.assertIn("a budget", text)
        self.assertNotIn("{kind}", text)
        self.assertNotIn("{about}", text)

    def test_unknown_prompt_is_an_error_not_a_crash(self):
        reply = m.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                          "params": {"name": "nope"}})
        self.assertIn("error", reply)

    def test_every_prompt_template_placeholder_is_declared(self):
        # a {placeholder} with no matching argument would reach the user raw
        import re
        for prompt in m.PROMPTS:
            declared = {a["name"] for a in prompt.get("arguments", [])}
            used = set(re.findall(r"\{([a-z_]+)\}", prompt["text"]))
            self.assertEqual(used - declared, set(),
                             "%s uses undeclared placeholders" % prompt["name"])


class CalcParityTest(unittest.TestCase):
    """Calc counterparts of the Writer supporting tools."""

    def test_new_calc_tools_registered(self):
        for name in ("calc_find", "calc_set_document_defaults",
                     "calc_set_header_footer"):
            self.assertIn(name, m.TOOLS)
            self.assertIn(name, {d["name"] for d in m.TOOL_DEFS})

    def test_calc_find_is_read_only_but_the_setters_are_not(self):
        self.assertIn("calc_find", m._NO_UNDO)
        for name in ("calc_set_document_defaults", "calc_set_header_footer"):
            self.assertNotIn(name, m._NO_UNDO)

    def test_calc_print_options_are_the_page_style_ones(self):
        # Calc keeps these on the PAGE STYLE; com.sun.star.document.Settings
        # carries only PrinterName/PrinterSetup, so naming a document-settings
        # property here raises UnknownPropertyException at the office
        for prop in ("PrintGrid", "PrintFormulas", "PrintHeaders",
                     "PrintCharts", "PrintZeroValues", "PrintDownFirst"):
            self.assertIn(prop, m._PRINT_SETTINGS_CALC)

    def test_calc_print_options_exclude_invented_names(self):
        # these were guessed in 0.9.6 and do not exist on a Calc page style
        for bogus in ("PrintAllSheets", "PrintEmptyPages", "PrintNotes"):
            self.assertNotIn(bogus, m._PRINT_SETTINGS_CALC, bogus)

    def test_writer_and_calc_print_lists_stay_disjoint(self):
        self.assertEqual(set(m._PRINT_SETTINGS_WRITER) & set(m._PRINT_SETTINGS_CALC),
                         set(), "an option in both lists would hide a routing bug")

    def test_calc_find_advertises_both_search_modes(self):
        props = {d["name"]: d for d in m.TOOL_DEFS}["calc_find"]["inputSchema"]["properties"]
        for key in ("search", "style", "regex", "sheet"):
            self.assertIn(key, props)


class LooksNumericTest(unittest.TestCase):
    def test_accepts_real_numbers(self):
        for text in ("3", "10.5", "-2", "+0.5", "1e3", ".5"):
            self.assertTrue(m._looks_numeric(text), text)

    def test_rejects_text_and_pythons_float_extras(self):
        # float() accepts all of these; a user does not mean them as numbers
        for text in ("", "abc", "nan", "inf", "-inf", "Infinity", "1_000", "3 4"):
            self.assertFalse(m._looks_numeric(text), text)


class CleanCellTest(unittest.TestCase):
    def test_trims_text_and_leaves_other_types_alone(self):
        self.assertEqual(m._clean_cell("  hi  "), "hi")
        self.assertEqual(m._clean_cell(""), "")
        self.assertEqual(m._clean_cell(3.5), 3.5)
        self.assertIsNone(m._clean_cell(None))

    def test_drops_calcs_force_text_marker_from_numeric_text(self):
        # getFormulaArray() renders numeric-LOOKING text with a leading "'";
        # leaving it in place is what kept " 3 " stuck as text (live-verified)
        self.assertEqual(m._clean_cell("' 3 "), "3")
        self.assertEqual(m._clean_cell("'10.5"), "10.5")
        self.assertEqual(m._clean_cell("'-2"), "-2")

    def test_keeps_the_marker_when_the_body_is_not_a_number(self):
        # dropping it here would silently change what the cell says
        self.assertEqual(m._clean_cell("'hello "), "'hello")
        self.assertEqual(m._clean_cell("'nan"), "'nan")


class PresetTest(unittest.TestCase):
    def test_preset_enums_match_the_implementations(self):
        by_name = {d["name"]: d for d in m.TOOL_DEFS}
        pairs = [("calc_format_table", m._TABLE_PRESETS),
                 ("writer_format_document", m._DOC_PRESETS)]
        for tool, presets in pairs:
            enum = by_name[tool]["inputSchema"]["properties"]["preset"]["enum"]
            self.assertEqual(sorted(enum), sorted(presets),
                             "%s advertises presets it cannot apply" % tool)


if __name__ == "__main__":
    unittest.main()
