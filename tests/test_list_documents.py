# SPDX-License-Identifier: MIT
"""list_documents returns the open documents and nothing else.

It used to delegate to tool_lo_status, which lives in another module and was
never imported, so every call raised NameError. Both tools now build the list
with core._open_docs_info. No UNO needed: the open documents are faked and
_doc_kind is stubbed.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import libreoffice_mcp as srv  # noqa: E402
import loconn.core as core  # noqa: E402


def _no_office():
    raise RuntimeError("offline unit test: never connect to or launch LibreOffice")


class _Doc:
    def __init__(self, title, url):
        self._title, self._url = title, url

    def getTitle(self):
        return self._title

    def getURL(self):
        return self._url


class TestListDocuments(unittest.TestCase):
    def setUp(self):
        docs = [_Doc("budget.ods", "file:///tmp/budget.ods"), _Doc("Untitled 1", "")]
        # Stub _bridge rather than _connect: each tool module holds its own
        # star-imported binding of _connect, but every one of them reaches UNO
        # through core._bridge, so no code path can connect or launch an office.
        for name, stub in (("_bridge", _no_office),
                           ("_open_docs", lambda: docs),
                           ("_doc_kind", lambda doc: "calc")):
            patcher = mock.patch.object(core, name, stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_returns_only_the_open_documents(self):
        self.assertEqual(srv.TOOLS["list_documents"]({}), {"documents": [
            {"title": "budget.ods", "type": "calc", "url": "file:///tmp/budget.ods"},
            {"title": "Untitled 1", "type": "calc", "url": ""}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
