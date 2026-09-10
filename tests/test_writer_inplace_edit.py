# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Offline regressions for issue #15 (Writer in-place-edit gaps).

The live behaviour is covered by tests/integration/test_writer_inplace_uno.py;
these lock the parts that need no office: the dispatch catalog really lists
EVERY registered tool (it used to list 16 — the module's own TOOL_DEFS shadowed
the registry's), the inline-markup parser, and the new schema arguments.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "mcp"))
import libreoffice_mcp  # noqa: E402,F401  (registers every tool)
from loconn import core, registry  # noqa: E402
from loconn.tools import shared_automation as sa  # noqa: E402


class DispatchCatalogTest(unittest.TestCase):

    def test_catalog_lists_every_registered_tool(self):
        cat = sa._catalog(None)
        names = {t["name"] for g in cat["groups"].values() for t in g}
        self.assertEqual(names, set(registry.TOOLS))
        self.assertEqual(cat["count"], len(registry.TOOLS))
        self.assertGreater(len(registry.TOOLS), 200)   # not just one module's slice

    def test_catalog_groups_by_application(self):
        groups = sa._catalog(None)["groups"]
        for g in ("writer", "calc", "impress", "draw", "document", "escape-hatch"):
            self.assertIn(g, groups, g)
        self.assertTrue(all(t["name"].startswith("writer_") for t in groups["writer"]))
        self.assertIn("uno_exec", {t["name"] for t in groups["escape-hatch"]})

    def test_filter_by_group_and_substring(self):
        w = sa._catalog("writer")
        self.assertEqual(list(w["groups"]), ["writer"])
        p = sa._catalog("paragraph")
        names = {t["name"] for g in p["groups"].values() for t in g}
        self.assertIn("writer_insert_paragraphs", names)
        self.assertIn("writer_delete_paragraphs", names)
        self.assertTrue(all("paragraph" in n for n in names))
        self.assertEqual(sa._catalog("no-such-thing")["count"], 0)

    def test_catalog_marks_the_advertised_tier(self):
        adv = {t["name"] for g in sa._catalog(None)["groups"].values()
               for t in g if t["advertised"]}
        self.assertEqual(adv, set(registry.BASIC_TOOLS))

    def test_usage_is_one_short_line(self):
        self.assertEqual(sa._usage("First. Second sentence."), "First")
        self.assertLessEqual(len(sa._usage("x" * 500)), 110)
        for g in sa._catalog(None)["groups"].values():
            for t in g:
                self.assertLessEqual(len(t["usage"]), 110, t["name"])

    def test_dispatch_list_goes_through_the_catalog(self):
        self.assertEqual(sa.tool_dispatch({"tool": "list"})["count"],
                         len(registry.TOOLS))
        self.assertEqual(sa.tool_dispatch({"filter": "calc"})["count"],
                         len(sa._catalog("calc")["groups"]["calc"]))


class MarkupParserTest(unittest.TestCase):

    def test_plain(self):
        self.assertEqual(core._parse_markup("plain"), [("plain", False, False, None)])

    def test_bold_italic_link(self):
        runs = core._parse_markup("a **b** *i* [t](http://u) z")
        self.assertEqual(runs, [("a ", False, False, None), ("b", True, False, None),
                                (" ", False, False, None), ("i", False, True, None),
                                (" ", False, False, None), ("t", False, False, "http://u"),
                                (" z", False, False, None)])

    def test_unclosed_marker_is_literal(self):
        self.assertEqual(core._parse_markup("**x"), [("**x", False, False, None)])

    def test_empty(self):
        self.assertEqual(core._parse_markup(""), [("", False, False, None)])


class SchemaTest(unittest.TestCase):
    """The issue's acceptance list, as schema facts."""

    def _props(self, name):
        d = next(d for d in registry.TOOL_DEFS if d["name"] == name)
        return d["inputSchema"]["properties"]

    def test_new_arguments_exist(self):
        for tool, keys in {
                "writer_append_text": ("at_index", "markup"),
                "writer_insert_heading": ("at_index", "markup"),
                "writer_insert_paragraphs": ("at_index", "paragraphs"),
                "writer_format_text": ("start", "count", "char_start", "char_end"),
                "writer_set_paragraph_text": ("markup",),
                "writer_apply_list": ("list_style", "level", "copy_from_index"),
                "writer_get_paragraphs": ("detail", "start", "count"),
                "writer_format_paragraph": ("font_size", "font_name", "bold",
                                            "italic", "font_color", "keep_with_next"),
                "writer_delete_paragraphs": ("include_tables",),
                "dispatch": ("filter",)}.items():
            props = self._props(tool)
            for k in keys:
                self.assertIn(k, props, "%s.%s" % (tool, k))

    def test_search_no_longer_required_on_format_text(self):
        d = next(d for d in registry.TOOL_DEFS if d["name"] == "writer_format_text")
        self.assertNotIn("search", d["inputSchema"].get("required", []))

    def test_page_map_is_read_only_and_hidden(self):
        self.assertIn("writer_page_map", registry.NO_UNDO)
        self.assertNotIn("writer_page_map", registry.BASIC_TOOLS)
        self.assertIn("writer_insert_paragraphs", registry.BASIC_TOOLS)


if __name__ == "__main__":
    unittest.main()
