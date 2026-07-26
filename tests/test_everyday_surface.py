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
                     "writer_format_document", "writer_find_replace"):
            self.assertNotIn(name, m._NO_UNDO,
                             "%s mutates — it must get an undo context" % name)

    def test_read_only_tools_are_exempt(self):
        for name in ("calc_read_range", "calc_overview", "lo_status",
                     "writer_get_text", "list_documents"):
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
