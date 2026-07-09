# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""LIVE end-to-end test: real LibreOffice Calc + a real Claude call.

The only test that exercises the full loop with the actual Messages API. It is
SKIPPED (exit 2) unless ANTHROPIC_API_KEY is set. Run via the harness:

    setx ANTHROPIC_API_KEY "sk-ant-..."   (then open a new shell)
    powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1 \
        -Test tests/integration/test_calc_end_to_end.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import uno_bridge as ub
from claude_client import ClaudeClient

PORT = int(os.environ.get("LO_UNO_PORT", "2002"))


def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("SKIP: set ANTHROPIC_API_KEY to run the live end-to-end test.")
        return 2

    ctx, smgr, desktop = ub.connect(port=PORT)
    print("Connected on port", PORT)
    doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, ())
    try:
        sheet = doc.getSheets().getByIndex(0)
        rng = sheet.getCellRangeByName("A1:A3")
        rng.setDataArray((("apple",), ("banana",), ("cherry",)))
        ub.select_range(doc, rng)

        client = ClaudeClient(api_key=key, model="claude-haiku-4-5")
        ub.transform_selection(
            doc, client,
            "Uppercase every cell. Return exactly the same 3-row by 1-column grid.")

        out = [row[0] for row in ub.read_range_grid(sheet.getCellRangeByName("A1:A3"))]
        print("result:", out)
        if out != ["APPLE", "BANANA", "CHERRY"]:
            raise AssertionError("unexpected transform result: %r" % (out,))
        print("\nPASS: live Claude transform through Calc UNO, end to end.")
        return 0
    finally:
        try:
            doc.close(False)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
