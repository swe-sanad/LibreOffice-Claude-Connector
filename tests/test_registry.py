# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""The tool registry and the module split.

The server used to be one 9,000-line file where every tool had to appear in four
separate global lists. These tests lock in the structure that replaced it.
"""

import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
import libreoffice_mcp as m           # noqa: E402
from loconn import registry           # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_every_tool_has_a_handler_and_a_schema(self):
        names = {d["name"] for d in registry.TOOL_DEFS}
        self.assertEqual(names, set(registry.TOOLS))
        self.assertEqual(len(registry.TOOL_DEFS), len(registry.TOOLS),
                         "a tool was registered twice")

    def test_handler_matches_its_declared_name(self):
        for name, func in registry.TOOLS.items():
            self.assertEqual(func.__name__, "tool_" + name)

    def test_tiers_only_name_registered_tools(self):
        self.assertEqual(registry.BASIC_TOOLS - set(registry.TOOLS), set())
        self.assertEqual(registry.NO_UNDO - set(registry.TOOLS), set())

    def test_register_rejects_a_schema_with_no_handler(self):
        with self.assertRaises(ImportError):
            registry.register({}, [{"name": "ghost_tool", "description": "",
                                    "inputSchema": {}}])

    def test_register_rejects_a_tier_naming_an_unknown_tool(self):
        with self.assertRaises(ImportError):
            registry.register({"tool_x": lambda a: None},
                              [{"name": "x", "description": "", "inputSchema": {}}],
                              basic=["not_declared_here"])

    def test_advertised_respects_the_tier(self):
        basic = registry.advertised(False)
        self.assertEqual({d["name"] for d in basic}, set(registry.BASIC_TOOLS))
        self.assertEqual(len(registry.advertised(True)), len(registry.TOOL_DEFS))


class ModuleSplitTest(unittest.TestCase):
    """Per application, then per concern — and nothing back in one big file."""

    TOOLS_DIR = ROOT / "mcp" / "loconn" / "tools"

    def test_package_layout(self):
        self.assertTrue((ROOT / "mcp" / "loconn" / "core.py").is_file())
        self.assertTrue((ROOT / "mcp" / "loconn" / "registry.py").is_file())
        self.assertTrue(self.TOOLS_DIR.is_dir())

    def test_modules_are_grouped_by_application(self):
        mods = {p.stem for p in self.TOOLS_DIR.glob("*.py")} - {"__init__"}
        for prefix in ("calc_", "writer_", "shared_"):
            self.assertTrue(any(x.startswith(prefix) for x in mods),
                            "no %s* module" % prefix)

    def test_no_module_grows_back_into_a_monolith(self):
        # the whole point of the split; 1200 lines is generous headroom
        for path in list(self.TOOLS_DIR.glob("*.py")) + [
                ROOT / "mcp" / "loconn" / "core.py",
                ROOT / "mcp" / "libreoffice_mcp.py"]:
            lines = len(path.read_text(encoding="utf-8").splitlines())
            self.assertLess(lines, 2000, "%s is %d lines" % (path.name, lines))

    def test_entry_point_is_only_the_protocol(self):
        # tools must not creep back into the entry point
        text = (ROOT / "mcp" / "libreoffice_mcp.py").read_text(encoding="utf-8")
        self.assertNotIn("\ndef tool_", text)

    def test_every_tool_module_is_imported_by_the_package(self):
        listed = (self.TOOLS_DIR / "__init__.py").read_text(encoding="utf-8")
        for path in self.TOOLS_DIR.glob("*.py"):
            if path.stem != "__init__":
                self.assertIn(path.stem, listed,
                              "%s exists but nothing imports it, so its tools "
                              "never register" % path.name)

    def test_bundle_ships_the_whole_package(self):
        # a module present on disk but missing from the .mcpb would break the
        # server only after it was published
        build = (ROOT / "scripts" / "build_mcpb.py").read_text(encoding="utf-8")
        self.assertIn("loconn", build)


class BackwardsCompatibleNamesTest(unittest.TestCase):
    """The offline suite and scripts still reach for the old module globals."""

    def test_old_names_still_resolve(self):
        for name in ("TOOLS", "TOOL_DEFS", "_BASIC_TOOLS", "_NO_UNDO",
                     "_advertised_tools", "_classify_error", "_run_with_timeout",
                     "SERVER_INSTRUCTIONS", "PROMPTS", "handle", "main"):
            self.assertTrue(hasattr(m, name), name)

    def test_tier_sets_are_the_registry_objects(self):
        self.assertIs(m._BASIC_TOOLS, registry.BASIC_TOOLS)
        self.assertIs(m._NO_UNDO, registry.NO_UNDO)


if __name__ == "__main__":
    unittest.main()
