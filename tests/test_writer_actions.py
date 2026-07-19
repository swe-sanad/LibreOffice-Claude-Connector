# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Offline unit tests for :mod:`writer_actions` (no UNO, no network, no key)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import writer_actions as wa  # noqa: E402


class _FakeResult:
    def __init__(self, text, truncated=False):
        self.text = text
        self.truncated = truncated


class _FakeClient:
    def __init__(self, reply, truncated=False):
        self.reply = reply
        self.truncated = truncated
        self.last_kwargs = None

    def send(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResult(self.reply, self.truncated)


class TestCleanOutput(unittest.TestCase):
    def test_trims(self):
        self.assertEqual(wa.clean_output("  hi  "), "hi")

    def test_unwraps_whole_fence(self):
        self.assertEqual(wa.clean_output("```\nhello world\n```"), "hello world")
        self.assertEqual(wa.clean_output("```text\nfoo\nbar\n```"), "foo\nbar")

    def test_preserves_inline_quotes_and_partial_backticks(self):
        # A legitimate quoted sentence must NOT be stripped.
        self.assertEqual(wa.clean_output('"Keep me quoted."'), '"Keep me quoted."')
        # Inline code is not a whole-output fence.
        self.assertEqual(wa.clean_output("use `x` here"), "use `x` here")

    def test_none(self):
        self.assertEqual(wa.clean_output(None), "")


class TestPrompts(unittest.TestCase):
    def test_rewrite_prompts(self):
        self.assertIn("selected", wa.build_rewrite_system_prompt().lower())
        u = wa.build_rewrite_user_prompt("some text", "make it formal")
        self.assertIn("make it formal", u)
        self.assertIn("some text", u)

    def test_generate_system(self):
        self.assertIn("cursor", wa.build_generate_system_prompt().lower())


class TestRewrite(unittest.TestCase):
    def test_happy(self):
        client = _FakeClient("```\nRewritten.\n```")
        out = wa.rewrite_text(client, "original", "improve", model="claude-haiku-4-5")
        self.assertEqual(out, "Rewritten.")           # fence stripped
        self.assertEqual(client.last_kwargs["model"], "claude-haiku-4-5")
        self.assertIn("improve", client.last_kwargs["prompt"])

    def test_empty_selection_raises(self):
        client = _FakeClient("x")
        with self.assertRaises(wa.WriterActionError):
            wa.rewrite_text(client, "   ", "do it")

    def test_truncated_response_gets_visible_note(self):
        client = _FakeClient("partial answer", truncated=True)
        out = wa.rewrite_text(client, "original", "expand")
        self.assertIn("partial answer", out)
        self.assertIn("cut off", out)               # note is appended, not silent

    def test_untruncated_has_no_note(self):
        client = _FakeClient("full answer", truncated=False)
        self.assertEqual(wa.rewrite_text(client, "original", "x"), "full answer")


class TestGenerate(unittest.TestCase):
    def test_happy(self):
        client = _FakeClient("Fresh text.")
        self.assertEqual(wa.generate_text(client, "write a haiku"), "Fresh text.")

    def test_empty_instruction_raises(self):
        client = _FakeClient("x")
        with self.assertRaises(wa.WriterActionError):
            wa.generate_text(client, "  ")


class TestDefaults(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(wa.default_max_tokens(""), 512)
        self.assertEqual(wa.default_max_tokens("x" * 100000), 8192)
        self.assertTrue(512 <= wa.default_max_tokens("a" * 400) <= 8192)


if __name__ == "__main__":
    unittest.main(verbosity=2)
