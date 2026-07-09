# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""LIVE integration test — drives a real LibreOffice Calc over UNO.

Requires a running office started with a socket accept, e.g.:

    soffice --headless --norestore \
        --accept="socket,host=localhost,port=2002;urp;" \
        -env:UserInstallation=file:///C:/temp/lo_test_profile

Run with the BUNDLED python so the ``uno`` module is importable:

    & "C:\\Program Files\\LibreOffice\\program\\python.exe" tests\\integration\\test_calc_uno.py

Uses NO API key: the Claude call is replaced by a deterministic stub so this
test exercises the UNO read/selection/write path in isolation. Exits non-zero
on any failure.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import uno_bridge as ub

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))


def stub_transform(grid):
    """Deterministic stand-in for Claude: uppercase text, +1 to numbers."""
    out = []
    for row in grid:
        new_row = []
        for v in row:
            if isinstance(v, bool):
                new_row.append(v)
            elif isinstance(v, str):
                new_row.append(v.upper())
            elif isinstance(v, (int, float)):
                new_row.append(v + 1)
            else:
                new_row.append(v)
        out.append(new_row)
    return out


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    ctx, smgr, desktop = ub.connect(port=PORT)
    print("Connected to LibreOffice on port", PORT)

    doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, ())
    try:
        _assert(ub.is_calc(doc), "loaded document is not a Calc doc")
        sheet = doc.getSheets().getByIndex(0)

        # 1) Seed A1:B2 and select it.
        rng = sheet.getCellRangeByName("A1:B2")
        rng.setDataArray((("hello", "world"), (1.0, 2.0)))
        ub.select_range(doc, rng)

        # 2) Read back through the selection normalizer.
        sel_range = ub.get_calc_selection_range(doc)
        _assert(sel_range is not None, "range selection returned None")
        grid = ub.read_range_grid(sel_range)
        _assert(grid == [["hello", "world"], [1.0, 2.0]], "read mismatch: %r" % (grid,))
        print("PASS: read a 2x2 range selection")

        # 3) Transform (stub) and write back.
        ub.write_range_grid(sel_range, stub_transform(grid))
        grid2 = ub.read_range_grid(sheet.getCellRangeByName("A1:B2"))
        _assert(grid2 == [["HELLO", "WORLD"], [2.0, 3.0]],
                "write/transform mismatch: %r" % (grid2,))
        print("PASS: wrote transformed grid back in one setDataArray call")

        # 4) Single-cell selection normalizes to a 1x1 range.
        ub.select_range(doc, sheet.getCellByPosition(0, 0))  # A1 == "HELLO"
        one = ub.get_calc_selection_range(doc)
        gone = ub.read_range_grid(one)
        _assert(gone == [["HELLO"]], "single-cell read mismatch: %r" % (gone,))
        print("PASS: single-cell selection -> 1x1 range")

        # 5) None coercion at the write boundary (would otherwise crash UNO).
        ub.write_range_grid(sheet.getCellRangeByName("A1"), [[None]])
        blank = sheet.getCellByPosition(0, 0).getString()
        _assert(blank == "", "None was not coerced to empty string: %r" % (blank,))
        print("PASS: None/null coerced to empty string on write")

        print("\nALL CALC UNO INTEGRATION CHECKS PASSED")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
