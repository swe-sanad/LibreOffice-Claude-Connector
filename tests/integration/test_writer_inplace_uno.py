# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LIVE test: the issue #15 Writer in-place-edit tools against a real office.

    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 `
        -Test tests/integration/test_writer_inplace_uno.py

Walks the CV scenario from the issue with ZERO uno_exec calls: read with
detail, delete a span (tables included), insert a styled block with markup,
reuse the document's own list rules, format runs by index, page map.
"""

import base64
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "mcp"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

import libreoffice_mcp as server  # noqa: E402

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _texts():
    return [p["text"] for p in server.tool_writer_get_paragraphs({})["paragraphs"]]


def main():
    os.environ["LO_UNO_PORT"] = str(PORT)
    server._desktop()
    tmpdir = tempfile.mkdtemp(prefix="lo_inplace_")
    server.tool_create_document({"type": "writer"})

    # --- §1 catalog -------------------------------------------------------
    cat = server.tool_dispatch({"tool": "list"})
    _assert(cat["count"] == len(server.TOOLS) > 200, cat["count"])
    _assert(server.tool_dispatch({"tool": "list", "filter": "writer"})["count"] > 50, "filter")
    print("PASS: dispatch list = every registered tool (%d)" % cat["count"])

    # --- §2 positional insert --------------------------------------------
    server.tool_writer_insert_heading({"text": "Title", "level": 1})
    server.tool_writer_append_text({"text": "P1\nP2\nP3"})
    _assert(_texts() == ["Title", "P1", "P2", "P3"], _texts())
    server.tool_writer_append_text({"text": "Before P2", "at_index": 2})
    server.tool_writer_insert_heading({"text": "Section", "level": 2, "at_index": 1})
    got = _texts()
    _assert(got == ["Title", "Section", "P1", "Before P2", "P2", "P3"], got)
    paras = server.tool_writer_get_paragraphs({})["paragraphs"]
    _assert(paras[1]["style"] == "Heading 2" and paras[3]["style"] == "Standard", paras)
    print("PASS: writer_append_text / writer_insert_heading at_index")

    # --- §2+§3 block insert with markup + list ----------------------------
    res = server.tool_writer_insert_paragraphs({
        "at_index": 3,
        "paragraphs": [
            {"text": "**Lead** plain *ital* [site](https://example.org)",
             "list": "bullet", "space_below_mm": 2},
            {"text": "Second bullet", "list": "bullet", "level": 1},
            {"text": "Plain after list", "style": "Standard"}]})
    _assert(res["inserted"] == 3 and res["next_index"] == 6, res)
    det = server.tool_writer_get_paragraphs({"start": 3, "count": 3, "detail": True})
    p3, p4, p5 = det["paragraphs"]
    _assert(p3["text"] == "Lead plain ital site", p3["text"])
    runs = {r["text"]: r for r in p3["runs"]}
    _assert(runs["Lead"]["bold"] and not runs["Lead"]["italic"], runs)
    _assert(runs["ital"]["italic"] and not runs["ital"]["bold"], runs)
    _assert(runs["site"].get("url") == "https://example.org", runs)
    _assert(not runs[" plain "]["bold"], "plain run inherited bold: %r" % runs)
    _assert("numbering" in p3 and p3["numbering"]["level"] == 0, p3)
    _assert(p4["numbering"]["level"] == 1, p4)
    _assert("numbering" not in p5, "list leaked into the plain paragraph: %r" % p5)
    _assert(abs(p3["space_below_mm"] - 2) < 0.2, p3)
    _assert(det["total"] == 9, det["total"])
    print("PASS: writer_insert_paragraphs (markup runs, list levels, spacing) + get_paragraphs detail")

    # --- §4 reuse the document's own list rules ---------------------------
    res = server.tool_writer_apply_list({"start": 5, "count": 1, "copy_from_index": 4})
    _assert(res["source"] == "copy_from_index" and res["level"] == 1, res)
    p5 = server.tool_writer_get_paragraphs({"start": 5, "count": 1, "detail": True})["paragraphs"][0]
    _assert(p5["numbering"]["level"] == 1, p5)
    print("PASS: writer_apply_list copy_from_index")

    # --- §3 format_text by index / char offsets ---------------------------
    # paragraph 6 is "P2"; make only "P" bold
    res = server.tool_writer_format_text({"start": 7, "char_start": 0, "char_end": 1,
                                          "bold": True})
    _assert(res["paragraphs_formatted"] == 1, res)
    p = server.tool_writer_get_paragraphs({"start": 7, "count": 1, "detail": True})["paragraphs"][0]
    _assert(p["text"] == "P2", p)
    _assert([(r["text"], r["bold"]) for r in p["runs"]] == [("P", True), ("2", False)], p["runs"])
    print("PASS: writer_format_text start/char_start/char_end")

    # --- §6 paragraph-level char props + keep_with_next -------------------
    res = server.tool_writer_format_paragraph({"start": 1, "count": 1, "font_size": 10.5,
                                               "keep_with_next": True})
    _assert("font_size" in res["applied"] and "keep_with_next" in res["applied"], res)
    p = server.tool_writer_get_paragraphs({"start": 1, "count": 1, "detail": True})["paragraphs"][0]
    _assert(p["keep_with_next"] is True, p)
    _assert(abs(p["runs"][0]["size_pt"] - 10.5) < 0.01, p["runs"])
    print("PASS: writer_format_paragraph font_size + keep_with_next")

    # --- §7 figures: anchor index + url ------------------------------------
    png = os.path.join(tmpdir, "dot.png")
    with open(png, "wb") as fh:
        fh.write(_PNG)
    server.tool_writer_insert_image({"path": png, "width_mm": 10, "height_mm": 10})
    figs = server.tool_writer_list_figures({})["figures"]
    _assert(len(figs) == 1, figs)
    name = figs[0]["name"]
    server.tool_set_alt_text({"name": name, "title": "Logo", "url": "https://example.org/p"})
    fig = server.tool_writer_list_figures({})["figures"][0]
    last = server.tool_writer_get_paragraphs({})["total"] - 1
    _assert(fig.get("anchor_index") == last, (fig, last))
    _assert(fig.get("url") == "https://example.org/p" and fig.get("title") == "Logo", fig)
    p = server.tool_writer_get_paragraphs({"start": last, "detail": True})["paragraphs"][0]
    _assert(p.get("anchored_objects") == [name], p)
    print("PASS: writer_list_figures anchor_index/url/title + anchored_objects")

    # --- §8 page map -------------------------------------------------------
    pm = server.tool_writer_page_map({})
    _assert(pm["pages"][0]["first_paragraph_index"] == 0, pm)
    _assert(pm["pages"][-1]["last_paragraph_index"] == last, pm)
    _assert(pm["page_count"] >= 1, pm)
    print("PASS: writer_page_map ->", pm["page_count"], "page(s)")

    # --- §9 delete a span that contains a table ---------------------------
    server.tool_writer_insert_table({"rows": 2, "columns": 2, "after_index": 2,
                                     "data": [["a", "b"], ["c", "d"]]})
    _assert(len(server.tool_writer_list_tables({})["tables"]) == 1, "table setup")
    n_before = server.tool_writer_get_paragraphs({})["total"]
    res = server.tool_writer_delete_paragraphs({"start": 2, "count": 3, "include_tables": True})
    _assert(len(res["tables_removed"]) == 1, res)
    _assert(server.tool_writer_list_tables({})["tables"] == [], "table survived")
    _assert(server.tool_writer_get_paragraphs({})["total"] == n_before - 3, "para count")
    print("PASS: writer_delete_paragraphs include_tables")

    server.tool_close_document({"save": False})
    print("\nALL WRITER IN-PLACE-EDIT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
