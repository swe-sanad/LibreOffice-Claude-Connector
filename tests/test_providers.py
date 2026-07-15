# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Offline unit tests for the OpenAI-compatible provider client (Ollama / LM
Studio / OpenRouter / ...). Mocks urllib — no network, no key, runs on 3.8+."""

import json
import os
import sys
import unittest
from email.message import Message
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import claude_client as cc          # noqa: E402  (transport lives here; patch its urlopen)
import providers                    # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        self.headers["content-type"] = "application/json; charset=utf-8"

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_OK = {
    "id": "chatcmpl-1", "model": "llama3",
    "choices": [{"message": {"role": "assistant", "content": "hi there"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


class TestOpenAICompatible(unittest.TestCase):
    def test_local_needs_no_key(self):
        # localhost + empty key must construct without raising
        providers.OpenAICompatibleClient(base_url="http://localhost:11434/v1")

    def test_parses_content(self):
        client = providers.OpenAICompatibleClient(api_key="k", model="llama3")
        with mock.patch.object(cc.urllib.request, "urlopen", return_value=_FakeResponse(_OK)):
            result = client.send(prompt="hi", max_tokens=16)
        self.assertEqual(result.text, "hi there")
        self.assertFalse(result.truncated)

    def test_length_finish_is_truncated(self):
        payload = json.loads(json.dumps(_OK))
        payload["choices"][0]["finish_reason"] = "length"
        client = providers.OpenAICompatibleClient(api_key="k")
        with mock.patch.object(cc.urllib.request, "urlopen", return_value=_FakeResponse(payload)):
            self.assertTrue(client.send(prompt="hi").truncated)

    def test_request_shape(self):
        """system -> a role:system message; POSTs to /chat/completions; bearer auth."""
        captured = {}

        def fake_urlopen(request, timeout=None, context=None):
            captured["url"] = request.full_url
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(_OK)

        client = providers.OpenAICompatibleClient(api_key="secret",
                                                  base_url="http://localhost:11434/v1")
        with mock.patch.object(cc.urllib.request, "urlopen", side_effect=fake_urlopen):
            client.send(prompt="hi", system="be terse", max_tokens=8, temperature=0.5)

        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["headers"]["authorization"], "Bearer secret")
        body = captured["body"]
        self.assertEqual(body["messages"],
                         [{"role": "system", "content": "be terse"},
                          {"role": "user", "content": "hi"}])
        self.assertEqual(body["temperature"], 0.5)

    def test_no_auth_header_without_key(self):
        captured = {}

        def fake_urlopen(request, timeout=None, context=None):
            captured["headers"] = {k.lower() for k, _ in request.header_items()}
            return _FakeResponse(_OK)

        client = providers.OpenAICompatibleClient(base_url="http://localhost:11434/v1")
        with mock.patch.object(cc.urllib.request, "urlopen", side_effect=fake_urlopen):
            client.send(prompt="hi")
        self.assertNotIn("authorization", captured["headers"])

    def test_http_error_raises_claude_error(self):
        def _err(*a, **k):
            import io, urllib.error
            hdrs = Message()
            return (_ for _ in ()).throw(urllib.error.HTTPError(
                url="http://localhost:11434/v1/chat/completions", code=401, msg="no",
                hdrs=hdrs, fp=io.BytesIO(b'{"error":{"message":"bad key"}}')))

        client = providers.OpenAICompatibleClient(api_key="x")
        with mock.patch.object(cc.urllib.request, "urlopen", side_effect=_err):
            with self.assertRaises(cc.ClaudeError):
                client.send(prompt="hi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
