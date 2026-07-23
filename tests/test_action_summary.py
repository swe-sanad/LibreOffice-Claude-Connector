# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Offline tests for the operator-facing action summary + the two-block
tools/call response (human narration first, JSON payload last). No UNO, no
network — imports the server module (uno is imported lazily inside handlers)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import libreoffice_mcp as srv  # noqa: E402


class TestActionSummary(unittest.TestCase):
    def test_scope_and_verb(self):
        s = srv._action_summary("writer_append_text", {"text": "hello"}, {"appended": True})
        self.assertTrue(s.startswith("Writer: append text"))
        self.assertIn("“hello”", s)
        self.assertIn("appended=True", s)

    def test_calc_scope_and_args(self):
        s = srv._action_summary("calc_write_range", {"range": "A1:B3", "sheet": 0},
                                {"cells_filled": 6})
        self.assertTrue(s.startswith("Calc: write range"))
        self.assertIn("range=A1:B3", s)
        self.assertIn("cells_filled=6", s)

    def test_result_fields_after_arrow(self):
        s = srv._action_summary("writer_set_text_direction", {"direction": "rtl"},
                                {"scope": "document", "paragraphs": 42})
        self.assertIn("direction=rtl", s)
        self.assertIn("→", s)
        self.assertIn("paragraphs=42", s)

    def test_long_text_is_truncated(self):
        s = srv._action_summary("writer_append_text", {"text": "x" * 500}, {})
        self.assertIn("…", s)
        self.assertLess(len(s), 200)

    def test_neutral_scope_for_cross_cutting(self):
        s = srv._action_summary("set_active_document", {"title": "proposal.odt"},
                                {"active": {"type": "writer"}})
        self.assertTrue(s.startswith("LibreOffice: set active document"))
        self.assertIn("title=proposal.odt", s)


class TestTwoBlockResponse(unittest.TestCase):
    def test_success_returns_summary_then_json(self):
        srv.TOOLS["_probe_ok"] = lambda a: {"cells_filled": 3, "range": "A1:C1"}
        try:
            resp = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "_probe_ok",
                                          "arguments": {"range": "A1:C1"}}})
            content = resp["result"]["content"]
            self.assertEqual(len(content), 2)
            self.assertIn("cells_filled=3", content[0]["text"])   # human summary
            payload = json.loads(content[-1]["text"])             # structured JSON
            self.assertEqual(payload["cells_filled"], 3)
            self.assertNotIn("isError", resp["result"])
        finally:
            srv.TOOLS.pop("_probe_ok", None)

    def test_error_is_single_readable_block(self):
        def boom(_a):
            raise ValueError("kaboom")
        srv.TOOLS["_probe_boom"] = boom
        try:
            resp = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "_probe_boom", "arguments": {}}})
            self.assertTrue(resp["result"].get("isError"))
            self.assertIn("kaboom", resp["result"]["content"][0]["text"])
            self.assertIn("ValueError", resp["result"]["content"][0]["text"])
        finally:
            srv.TOOLS.pop("_probe_boom", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
