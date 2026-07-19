# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LIVE integration test — drives a real LibreOffice Writer over UNO.

Run via the shared harness (starts an isolated headless office):

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_writer_uno.py

Uses NO API key: exercises the selection-read / replace / multi-paragraph /
insert-at-caret UNO paths directly. Exits non-zero on any failure.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import uno_bridge as ub

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _paragraphs(xtext):
    out = []
    en = xtext.createEnumeration()
    while en.hasMoreElements():
        para = en.nextElement()
        if para.supportsService("com.sun.star.text.Paragraph"):
            out.append(para.getString())
    return out


def main():
    ctx, smgr, desktop = ub.connect(port=PORT)
    print("Connected to LibreOffice on port", PORT)

    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, ())
    try:
        _assert(ub.is_writer(doc), "loaded document is not a Writer doc")
        body = doc.getText()
        controller = doc.getCurrentController()
        vc = controller.getViewCursor()

        # 1) Seed text and select all of it.
        body.insertString(body.createTextCursor(), "The quick brown fox.", False)
        vc.gotoStart(False)
        vc.gotoEnd(True)
        text, has_sel = ub.get_writer_selection(doc)
        _assert(text == "The quick brown fox.", "selection read mismatch: %r" % (text,))
        _assert(has_sel is True, "expected has_selection True")
        print("PASS: read the selected text")

        # 2) Replace the selection.
        ub.replace_writer_selection(doc, "A slow green turtle.")
        _assert(body.getString() == "A slow green turtle.",
                "replace mismatch: %r" % (body.getString(),))
        print("PASS: replaced selection in place")

        # 3) Multi-paragraph replacement -> two real paragraphs.
        vc.gotoStart(False)
        vc.gotoEnd(True)
        ub.replace_writer_selection(doc, "Line one\nLine two")
        paras = _paragraphs(body)
        _assert(paras == ["Line one", "Line two"],
                "expected two paragraphs, got %r" % (paras,))
        print("PASS: newline became a real paragraph break")

        # 4) No selection -> has_selection is False; insert at caret.
        vc.gotoEnd(False)
        _, has_sel2 = ub.get_writer_selection(doc)
        _assert(has_sel2 is False, "expected has_selection False after collapse")
        ub.insert_writer_at_caret(doc, " (appended)")
        _assert(body.getString().endswith("(appended)"),
                "insert-at-caret mismatch: %r" % (body.getString(),))
        print("PASS: caret detection + insert-at-caret")

        print("\nALL WRITER UNO INTEGRATION CHECKS PASSED")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
