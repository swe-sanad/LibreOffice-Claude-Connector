# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Offline unit tests for :mod:`claude_client`.

These mock ``urllib`` entirely, so they need **no API key and no network** and
run on LibreOffice's bundled Python (``python.exe -m unittest``) or any 3.8+.
"""

import io
import json
import os
import sys
import unittest
import urllib.error
from email.message import Message
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import claude_client as cc  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeResponse:
    """Minimal stand-in for the object returned by urlopen()."""

    def __init__(self, payload: dict, charset: str = "utf-8"):
        self._bytes = json.dumps(payload).encode(charset)
        self.headers = Message()
        self.headers["content-type"] = "application/json; charset=%s" % charset

    def read(self) -> bytes:
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(status: int, error_type: str, message: str, retry_after=None):
    hdrs = Message()
    if retry_after is not None:
        hdrs["retry-after"] = str(retry_after)
    body = json.dumps({"type": "error", "error": {"type": error_type, "message": message}})
    return urllib.error.HTTPError(
        url=cc.DEFAULT_BASE_URL, code=status, msg=message,
        hdrs=hdrs, fp=io.BytesIO(body.encode("utf-8")),
    )


_OK_PAYLOAD = {
    "id": "msg_123",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 2},
}


class _SleepSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _client(**kw):
    kw.setdefault("api_key", "sk-ant-test")
    kw.setdefault("sleep", _SleepSpy())
    return cc.ClaudeClient(**kw)


# --------------------------------------------------------------------------- #
# Construction / validation
# --------------------------------------------------------------------------- #

class TestConstruction(unittest.TestCase):
    def test_missing_key_raises(self):
        with self.assertRaises(cc.ClaudeConfigError):
            cc.ClaudeClient(api_key="")
        with self.assertRaises(cc.ClaudeConfigError):
            cc.ClaudeClient(api_key="   ")

    def test_bad_timeout_and_retries(self):
        with self.assertRaises(cc.ClaudeConfigError):
            cc.ClaudeClient(api_key="k", timeout=0)
        with self.assertRaises(cc.ClaudeConfigError):
            cc.ClaudeClient(api_key="k", max_retries=-1)

    def test_prompt_xor_messages(self):
        client = _client()
        with self.assertRaises(cc.ClaudeConfigError):
            client.send()  # neither
        with self.assertRaises(cc.ClaudeConfigError):
            client.send(prompt="hi", messages=[{"role": "user", "content": "hi"}])

    def test_bad_max_tokens(self):
        client = _client()
        with self.assertRaises(cc.ClaudeConfigError):
            client.send(prompt="hi", max_tokens=0)


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #

class TestExtractText(unittest.TestCase):
    def test_joins_only_text_blocks(self):
        payload = {"content": [
            {"type": "text", "text": "A"},
            {"type": "tool_use", "id": "x", "name": "f", "input": {}},
            {"type": "text", "text": "B"},
        ]}
        self.assertEqual(cc.extract_text(payload), "AB")

    def test_empty_and_missing(self):
        self.assertEqual(cc.extract_text({}), "")
        self.assertEqual(cc.extract_text({"content": None}), "")


# --------------------------------------------------------------------------- #
# send() happy path
# --------------------------------------------------------------------------- #

class TestSendSuccess(unittest.TestCase):
    def test_success_parses_result(self):
        client = _client()
        with mock.patch.object(cc.urllib.request, "urlopen",
                               return_value=_FakeResponse(_OK_PAYLOAD)):
            result = client.send(prompt="hi", max_tokens=32)
        self.assertEqual(result.text, "Hello world")
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertEqual(result.model, "claude-sonnet-5")
        self.assertEqual(result.input_tokens, 10)
        self.assertEqual(result.output_tokens, 2)
        self.assertFalse(result.truncated)

    def test_truncated_flag(self):
        payload = dict(_OK_PAYLOAD, stop_reason="max_tokens")
        client = _client()
        with mock.patch.object(cc.urllib.request, "urlopen",
                               return_value=_FakeResponse(payload)):
            result = client.send(prompt="hi")
        self.assertTrue(result.truncated)

    def test_request_shape_system_is_top_level(self):
        """Anthropic shape: system is a top-level field, headers are correct."""
        captured = {}

        def fake_urlopen(request, timeout=None, context=None):
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["method"] = request.get_method()
            return _FakeResponse(_OK_PAYLOAD)

        client = _client(model="claude-haiku-4-5")
        with mock.patch.object(cc.urllib.request, "urlopen", side_effect=fake_urlopen):
            client.send(prompt="hi", system="be terse", max_tokens=16, temperature=0.5)

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["headers"]["x-api-key"], "sk-ant-test")
        self.assertEqual(captured["headers"]["anthropic-version"],
                         cc.DEFAULT_ANTHROPIC_VERSION)
        body = captured["body"]
        self.assertEqual(body["model"], "claude-haiku-4-5")
        self.assertEqual(body["system"], "be terse")          # top-level, not a message
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertNotIn("system", [m.get("role") for m in body["messages"]])
        self.assertEqual(body["temperature"], 0.5)


# --------------------------------------------------------------------------- #
# Error mapping & retries
# --------------------------------------------------------------------------- #

class TestErrors(unittest.TestCase):
    def test_401_maps_to_auth_error_no_retry(self):
        client = _client(max_retries=3)
        opener = mock.Mock(side_effect=_http_error(401, "authentication_error", "bad key"))
        with mock.patch.object(cc.urllib.request, "urlopen", opener):
            with self.assertRaises(cc.ClaudeAuthError):
                client.send(prompt="hi")
        self.assertEqual(opener.call_count, 1)  # auth errors are not retried

    def test_429_retries_then_raises_rate_limit(self):
        sleep = _SleepSpy()
        client = _client(max_retries=2, sleep=sleep)
        opener = mock.Mock(side_effect=_http_error(429, "rate_limit_error", "slow", retry_after=0))
        with mock.patch.object(cc.urllib.request, "urlopen", opener):
            with self.assertRaises(cc.ClaudeRateLimitError) as ctx:
                client.send(prompt="hi")
        self.assertEqual(opener.call_count, 3)      # initial + 2 retries
        self.assertEqual(len(sleep.calls), 2)       # slept between retries
        self.assertEqual(ctx.exception.retry_after, 0.0)

    def test_500_retries_then_succeeds(self):
        client = _client(max_retries=2)
        opener = mock.Mock(side_effect=[
            _http_error(500, "api_error", "boom"),
            _FakeResponse(_OK_PAYLOAD),
        ])
        with mock.patch.object(cc.urllib.request, "urlopen", opener):
            result = client.send(prompt="hi")
        self.assertEqual(result.text, "Hello world")
        self.assertEqual(opener.call_count, 2)

    def test_400_maps_to_api_error(self):
        client = _client()
        opener = mock.Mock(side_effect=_http_error(400, "invalid_request_error", "nope"))
        with mock.patch.object(cc.urllib.request, "urlopen", opener):
            with self.assertRaises(cc.ClaudeAPIError) as ctx:
                client.send(prompt="hi")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.error_type, "invalid_request_error")

    def test_network_error_retries_then_raises(self):
        client = _client(max_retries=1)
        opener = mock.Mock(side_effect=urllib.error.URLError("no route"))
        with mock.patch.object(cc.urllib.request, "urlopen", opener):
            with self.assertRaises(cc.ClaudeNetworkError):
                client.send(prompt="hi")
        self.assertEqual(opener.call_count, 2)  # initial + 1 retry


if __name__ == "__main__":
    unittest.main(verbosity=2)
