# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""UNO glue for LibreOffice Calc — connection + selection read/write.

This module imports ``uno`` and therefore only runs under an interpreter that
can see LibreOffice's UNO runtime (its bundled ``python.exe``, or a Python with
the office ``program`` dir on ``PYTHONPATH``). The pure transform logic lives in
:mod:`calc_actions` and is imported here only for cell coercion.

Two entry points:
  * :func:`connect` — resolve a running office over a socket (dev / testing).
  * Inside a real extension you already have the document; use the selection
    helpers directly with ``XSCRIPTCONTEXT.getDocument()``.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, Sequence, Tuple

import uno  # provided by LibreOffice's runtime

import calc_actions

Grid = List[List[Any]]

CALC_DOC_SERVICE = "com.sun.star.sheet.SpreadsheetDocument"


# --------------------------------------------------------------------------- #
# Connection (dev / test over a socket)
# --------------------------------------------------------------------------- #

def connect(host: str = "localhost", port: int = 2002,
            retries: int = 20, delay: float = 0.5) -> Tuple[Any, Any, Any]:
    """Resolve a running ``soffice`` that was started with ``--accept=socket,...``.

    Returns ``(ctx, service_manager, desktop)``. Retries because the office may
    still be opening its socket when we first try.
    """
    local_ctx = uno.getComponentContext()
    resolver = local_ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_ctx)
    url = ("uno:socket,host=%s,port=%d;urp;StarOffice.ComponentContext"
           % (host, port))
    last_err: Optional[Exception] = None
    for _ in range(retries):
        try:
            ctx = resolver.resolve(url)
            smgr = ctx.ServiceManager
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            return ctx, smgr, desktop
        except Exception as exc:  # NoConnectException etc. while office boots
            last_err = exc
            time.sleep(delay)
    raise RuntimeError("Could not connect to LibreOffice at %s:%d (%s)"
                       % (host, port, last_err))


def is_calc(doc: Any) -> bool:
    return bool(doc) and doc.supportsService(CALC_DOC_SERVICE)


# --------------------------------------------------------------------------- #
# Selection -> range
# --------------------------------------------------------------------------- #

def get_calc_selection_range(doc: Any) -> Optional[Any]:
    """Return a rectangular cell range for the current selection, or ``None``.

    Normalizes the three cell-like selection shapes to a single ``XCellRange``:
      * ``SheetCell``       -> a 1x1 range at that cell,
      * ``SheetCellRange``  -> itself,
      * ``SheetCellRanges`` -> the FIRST contained range.
    Returns ``None`` when a non-cell object (shape, chart, ...) is selected.
    """
    sel = doc.getCurrentSelection()
    if sel is None:
        return None

    if sel.supportsService("com.sun.star.sheet.SheetCell"):
        addr = sel.getCellAddress()
        sheet = doc.getSheets().getByIndex(addr.Sheet)
        return sheet.getCellRangeByPosition(addr.Column, addr.Row,
                                            addr.Column, addr.Row)

    if sel.supportsService("com.sun.star.sheet.SheetCellRange"):
        return sel

    if sel.supportsService("com.sun.star.sheet.SheetCellRanges"):
        addrs = sel.getRangeAddresses()
        if not addrs:
            return None
        a = addrs[0]
        sheet = doc.getSheets().getByIndex(a.Sheet)
        return sheet.getCellRangeByPosition(a.StartColumn, a.StartRow,
                                            a.EndColumn, a.EndRow)

    return None


# --------------------------------------------------------------------------- #
# Read / write
# --------------------------------------------------------------------------- #

def read_range_grid(cell_range: Any) -> Grid:
    """Read a range's contents into a list-of-lists (numbers->float, text->str)."""
    return [list(row) for row in cell_range.getDataArray()]


def write_range_grid(cell_range: Any, grid: Sequence[Sequence[Any]]) -> None:
    """Write a grid back via ``setDataArray`` (defensively coercing each cell).

    ``setDataArray`` throws on ``None`` or a dimension mismatch, so we coerce
    every cell (``None``/``null`` -> ``""``) here even though the caller should
    already have done so — belt and suspenders at the UNO boundary.
    """
    data = tuple(
        tuple(calc_actions.coerce_out_cell(v) for v in row)
        for row in grid
    )
    cell_range.setDataArray(data)


def select_range(doc: Any, cell_range: Any) -> None:
    """Select a range in the view (used by tests / to show the user the target)."""
    doc.getCurrentController().select(cell_range)


# --------------------------------------------------------------------------- #
# High-level (synchronous) flow
# --------------------------------------------------------------------------- #

def transform_selection(doc: Any, client: Any, instruction: str, **kwargs: Any) -> Any:
    """Read the selection, transform it with Claude, write it back.

    Synchronous — suitable for a menu-triggered macro. The packaged extension
    splits this so only the network call runs off the UI thread (read + write
    stay on the main thread). Returns the range that was written.
    """
    cell_range = get_calc_selection_range(doc)
    if cell_range is None:
        raise RuntimeError("Select one or more spreadsheet cells first.")
    grid = read_range_grid(cell_range)
    new_grid = calc_actions.transform_range(client, grid, instruction, **kwargs)
    write_range_grid(cell_range, new_grid)
    return cell_range
