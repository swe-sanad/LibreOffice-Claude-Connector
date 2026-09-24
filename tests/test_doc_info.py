# SPDX-License-Identifier: MIT
"""_doc_info must return a JSON-safe string title for every listed component.

A chart being edited in place is listed among the desktop's documents, and its
getTitle() is XChartDocument.getTitle(), which returns the title *shape*, not a
string. That object made lo_status and list_documents crash while serialising
the reply. No UNO needed: the component is faked and _doc_kind is stubbed.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import libreoffice_mcp  # noqa: E402,F401 - imports the server modules
import loconn.core as core  # noqa: E402


class _Shape:
    """Stands in for the pyuno XShape proxy a chart returns from getTitle()."""


class _Frame:
    def __init__(self, title):
        self._title = title

    def getTitle(self):
        return self._title


class _Controller:
    def __init__(self, frame_title):
        self._frame = _Frame(frame_title)

    def getFrame(self):
        return self._frame


class _Component:
    def __init__(self, title, url="", frame_title=None):
        self._title, self._url, self._frame_title = title, url, frame_title

    def getTitle(self):
        return self._title

    def getURL(self):
        return self._url

    def getCurrentController(self):
        return None if self._frame_title is None else _Controller(self._frame_title)


class TestDocInfoTitle(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(core, "_doc_kind", lambda doc: "other")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_chart_title_shape_falls_back_to_frame_title(self):
        info = core._doc_info(_Component(_Shape(), frame_title="Chart in budget.ods"))
        self.assertEqual(info["title"], "Chart in budget.ods")
        json.dumps(info)

    def test_chart_without_controller_or_url_gets_placeholder(self):
        info = core._doc_info(_Component(_Shape()))
        self.assertEqual(info["title"], "?")
        json.dumps(info)

    def test_string_title_is_unchanged(self):
        info = core._doc_info(_Component("budget.ods", url="file:///tmp/budget.ods"))
        self.assertEqual(info["title"], "budget.ods")


if __name__ == "__main__":
    unittest.main(verbosity=2)
