# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Offline tests for :mod:`config` and :mod:`keystore`.

Uses a temporary base directory so nothing touches the real user profile. On
Windows the keystore tests exercise real DPAPI encrypt/decrypt.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config as cfgmod  # noqa: E402
import keystore  # noqa: E402


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_when_missing(self):
        cfg = cfgmod.load_config(self.base)
        self.assertEqual(cfg["model"], cfgmod.DEFAULTS["model"])
        self.assertEqual(cfg["timeout"], 120.0)

    def test_save_then_load_roundtrip(self):
        cfg = cfgmod.load_config(self.base)
        cfg["model"] = "claude-opus-4-8"
        cfg["temperature"] = 0.3
        path = cfgmod.save_config(cfg, self.base)
        self.assertTrue(os.path.exists(path))
        again = cfgmod.load_config(self.base)
        self.assertEqual(again["model"], "claude-opus-4-8")
        self.assertEqual(again["temperature"], 0.3)

    def test_unknown_keys_ignored(self):
        cfgmod.save_config({"model": "claude-sonnet-5", "evil": "x"}, self.base)
        loaded = cfgmod.load_config(self.base)
        self.assertNotIn("evil", loaded)

    def test_corrupt_file_falls_back(self):
        os.makedirs(cfgmod.config_dir(self.base), exist_ok=True)
        with open(cfgmod.config_path(self.base), "w", encoding="utf-8") as handle:
            handle.write("{ not valid json ")
        cfg = cfgmod.load_config(self.base)  # must not raise
        self.assertEqual(cfg["model"], cfgmod.DEFAULTS["model"])

    def test_client_kwargs(self):
        cfg = cfgmod.load_config(self.base)
        kw = cfgmod.client_kwargs(cfg)
        self.assertEqual(kw["model"], cfgmod.DEFAULTS["model"])
        self.assertIn("base_url", kw)
        self.assertNotIn("api_key", kw)

    def _write_raw(self, obj):
        os.makedirs(cfgmod.config_dir(self.base), exist_ok=True)
        with open(cfgmod.config_path(self.base), "w", encoding="utf-8") as handle:
            json.dump(obj, handle)

    def test_coerces_bad_types(self):
        self._write_raw({"timeout": "120", "max_tokens": "50",
                         "temperature": "abc", "model": 5})
        cfg = cfgmod.load_config(self.base)
        self.assertEqual(cfg["timeout"], 120.0)                  # str -> float
        self.assertEqual(cfg["max_tokens"], 50)                  # str -> int
        self.assertIsNone(cfg["temperature"])                   # unparseable -> None
        self.assertEqual(cfg["model"], cfgmod.DEFAULTS["model"])  # non-str -> default

    def test_bad_timeout_falls_back(self):
        self._write_raw({"timeout": "not-a-number"})
        self.assertEqual(cfgmod.load_config(self.base)["timeout"], 120.0)
        self._write_raw({"timeout": 0})                          # non-positive
        self.assertEqual(cfgmod.load_config(self.base)["timeout"], 120.0)


class TestKeystore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(keystore.ENV_VAR, None)  # ensure no ambient key

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_set_get_roundtrip(self):
        self.assertIsNone(keystore.get_api_key(self.base))
        self.assertFalse(keystore.has_stored_key(self.base))
        keystore.set_api_key("sk-ant-secret-123", self.base)
        self.assertTrue(keystore.has_stored_key(self.base))
        self.assertEqual(keystore.get_api_key(self.base), "sk-ant-secret-123")

    def test_stored_ciphertext_is_not_plaintext_on_windows(self):
        keystore.set_api_key("sk-ant-DO-NOT-LEAK", self.base)
        if not keystore.IS_WINDOWS:
            self.skipTest("DPAPI is Windows-only")
        path = keystore._win_path(self.base)
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"sk-ant-DO-NOT-LEAK", raw)  # encrypted at rest

    def test_env_var_takes_precedence(self):
        keystore.set_api_key("sk-ant-stored", self.base)
        os.environ[keystore.ENV_VAR] = "sk-ant-from-env"
        self.assertEqual(keystore.get_api_key(self.base), "sk-ant-from-env")

    def test_clear(self):
        keystore.set_api_key("sk-ant-x", self.base)
        keystore.clear_api_key(self.base)
        self.assertFalse(keystore.has_stored_key(self.base))
        self.assertIsNone(keystore.get_api_key(self.base))

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            keystore.set_api_key("   ", self.base)


if __name__ == "__main__":
    unittest.main(verbosity=2)
