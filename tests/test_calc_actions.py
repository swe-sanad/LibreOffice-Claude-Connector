# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Offline unit tests for :mod:`calc_actions` (no UNO, no network, no key)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import calc_actions as ca  # noqa: E402


class _FakeResult:
    def __init__(self, text, truncated=False):
        self.text = text
        self.truncated = truncated


class _FakeClient:
    """Records the last send() kwargs and returns a canned reply."""

    def __init__(self, reply_text, truncated=False):
        self.reply_text = reply_text
        self.truncated = truncated
        self.last_kwargs = None

    def send(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResult(self.reply_text, self.truncated)


# --------------------------------------------------------------------------- #

class TestCoerce(unittest.TestCase):
    def test_values(self):
        self.assertEqual(ca.coerce_out_cell(None), "")
        self.assertEqual(ca.coerce_out_cell(True), "TRUE")
        self.assertEqual(ca.coerce_out_cell(False), "FALSE")
        self.assertEqual(ca.coerce_out_cell(3), 3.0)
        self.assertIsInstance(ca.coerce_out_cell(3), float)
        self.assertEqual(ca.coerce_out_cell(3.5), 3.5)
        self.assertEqual(ca.coerce_out_cell("hi"), "hi")


class TestPrompts(unittest.TestCase):
    def test_system_prompt_has_dims(self):
        p = ca.build_system_prompt(2, 3)
        self.assertIn("2 rows", p)
        self.assertIn("3 columns", p)
        self.assertIn('"cells"', p)

    def test_user_prompt_has_instruction_and_grid(self):
        p = ca.build_user_prompt([["a", 1.0]], "uppercase please")
        self.assertIn("uppercase please", p)
        self.assertIn('[["a", 1.0]]', p)
        self.assertIn("1 rows x 2 columns", p)


class TestParseGrid(unittest.TestCase):
    def test_plain_object(self):
        out = ca.parse_grid('{"cells": [["A", "B"], ["C", "D"]]}', 2, 2)
        self.assertEqual(out, [["A", "B"], ["C", "D"]])

    def test_bare_array(self):
        out = ca.parse_grid('[["A"]]', 1, 1)
        self.assertEqual(out, [["A"]])

    def test_markdown_fence(self):
        text = '```json\n{"cells": [["X"]]}\n```'
        self.assertEqual(ca.parse_grid(text, 1, 1), [["X"]])

    def test_surrounding_prose(self):
        text = 'Sure! Here is your grid:\n{"cells": [[1, 2]]}\nHope that helps.'
        self.assertEqual(ca.parse_grid(text, 1, 2), [[1.0, 2.0]])

    def test_null_becomes_empty_string(self):
        out = ca.parse_grid('{"cells": [[null, "x"]]}', 1, 2)
        self.assertEqual(out, [["", "x"]])

    def test_wrong_row_count(self):
        with self.assertRaises(ca.TransformError):
            ca.parse_grid('{"cells": [["A"]]}', 2, 1)

    def test_wrong_col_count(self):
        with self.assertRaises(ca.TransformError):
            ca.parse_grid('{"cells": [["A", "B"]]}', 1, 1)

    def test_missing_cells_key(self):
        with self.assertRaises(ca.TransformError):
            ca.parse_grid('{"data": [["A"]]}', 1, 1)

    def test_invalid_json(self):
        with self.assertRaises(ca.TransformError):
            ca.parse_grid("not json at all", 1, 1)

    def test_row_not_a_list(self):
        with self.assertRaises(ca.TransformError):
            ca.parse_grid('{"cells": ["A"]}', 1, 1)

    def test_bare_array_with_braces_in_cell(self):
        # Regression: stray braces inside a bare-array cell must NOT break parsing.
        out = ca.parse_grid('[["{name}", "y"]]', 1, 2)
        self.assertEqual(out, [["{name}", "y"]])

    def test_object_with_prose_containing_braces(self):
        text = 'Here you go {ok}: {"cells": [["a", "b"]]}'
        self.assertEqual(ca.parse_grid(text, 1, 2), [["a", "b"]])


class TestTransformRange(unittest.TestCase):
    def test_happy_path_and_kwargs(self):
        client = _FakeClient('{"cells": [["HELLO", "WORLD"]]}')
        out = ca.transform_range(client, (("hello", "world"),), "uppercase",
                                 model="claude-haiku-4-5")
        self.assertEqual(out, [["HELLO", "WORLD"]])
        # verify orchestration passed a system prompt + the model through
        self.assertEqual(client.last_kwargs["model"], "claude-haiku-4-5")
        self.assertIn("1 rows", client.last_kwargs["system"])
        self.assertIn("uppercase", client.last_kwargs["prompt"])
        self.assertGreater(client.last_kwargs["max_tokens"], 0)

    def test_empty_selection_raises(self):
        client = _FakeClient('{"cells": []}')
        with self.assertRaises(ca.TransformError):
            ca.transform_range(client, (), "do something")

    def test_shape_mismatch_from_model_raises(self):
        client = _FakeClient('{"cells": [["only one row"]]}')
        with self.assertRaises(ca.TransformError):
            ca.transform_range(client, (("a",), ("b",)), "x")  # expects 2x1

    def test_oversized_selection_raises_before_calling_model(self):
        client = _FakeClient('{"cells": []}')  # would fail if actually called
        big = tuple((i,) for i in range(ca.MAX_CELLS + 1))  # (MAX_CELLS+1) x 1
        with self.assertRaises(ca.TransformError):
            ca.transform_range(client, big, "x")
        self.assertIsNone(client.last_kwargs)  # never sent

    def test_truncated_response_raises(self):
        client = _FakeClient('{"cells": [["A"]]}', truncated=True)
        with self.assertRaises(ca.TransformError):
            ca.transform_range(client, (("a",),), "x")


class TestDefaults(unittest.TestCase):
    def test_max_tokens_bounds(self):
        self.assertEqual(ca.default_max_tokens(1, 1), 512)      # floor
        self.assertEqual(ca.default_max_tokens(100, 100), 8192)  # ceiling
        self.assertTrue(512 <= ca.default_max_tokens(10, 5) <= 8192)


if __name__ == "__main__":
    unittest.main(verbosity=2)
