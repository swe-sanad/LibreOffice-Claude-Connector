# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Calc "transform the selected range" logic — pure, UNO-free, testable.

This module contains **no** UNO and **no** networking imports, so it can be
unit-tested on any Python 3.8+ with no LibreOffice and no API key. The UNO glue
that reads the selection and writes the result lives in ``uno_bridge`` and is
tested against a real LibreOffice instance.

Flow:  grid (2-D) + instruction  ->  prompt  ->  Claude  ->  same-shaped grid.

The hard part is making Claude reliably return a grid of the *exact* same
dimensions. We do two things: (1) an explicit, strict system prompt, and
(2) a tolerant parser that survives markdown fences and stray prose.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Sequence

Grid = List[List[Any]]

__all__ = [
    "TransformError",
    "coerce_out_cell",
    "normalize_grid",
    "build_system_prompt",
    "build_user_prompt",
    "parse_grid",
    "transform_range",
]


class TransformError(Exception):
    """Claude's response could not be turned into a correctly-shaped grid."""


# --------------------------------------------------------------------------- #
# Cell / grid coercion
# --------------------------------------------------------------------------- #

def coerce_out_cell(value: Any) -> Any:
    """Coerce one value into something ``setDataArray`` accepts (str or number).

    LibreOffice's ``setDataArray`` rejects ``None`` and non-primitive types, so:
      * ``None``          -> ``""``  (the #1 AI-output crash: JSON null -> None)
      * ``bool``          -> ``"TRUE"``/``"FALSE"`` (bool is a subclass of int;
                             handle it before the numeric branch)
      * ``int`` / ``float`` -> ``float``
      * anything else     -> ``str``
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)


def normalize_grid(data: Sequence[Sequence[Any]]) -> Grid:
    """Turn UNO's tuple-of-tuples (or any nested sequence) into a list-of-lists."""
    return [list(row) for row in data]


def grid_dimensions(grid: Sequence[Sequence[Any]]) -> "tuple[int, int]":
    nrows = len(grid)
    ncols = len(grid[0]) if nrows else 0
    return nrows, ncols


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #

def build_system_prompt(nrows: int, ncols: int) -> str:
    return (
        "You transform spreadsheet data. You are given a 2-D grid of cells as JSON "
        "(an array of {rows} rows, each with {cols} columns) and an instruction from "
        "the user.\n\n"
        "Respond with ONLY a JSON object of the exact form:\n"
        '  {{"cells": [[...], [...]]}}\n\n'
        "Rules you MUST follow:\n"
        "- The result grid MUST have EXACTLY {rows} rows and EXACTLY {cols} columns "
        "(the same shape as the input).\n"
        "- Keep numbers as JSON numbers and text as JSON strings.\n"
        "- Use an empty string \"\" for a cell that should be blank; never use null.\n"
        "- Output the JSON object only: no explanation, no markdown, no code fences."
    ).format(rows=nrows, cols=ncols)


def build_user_prompt(grid: Grid, instruction: str) -> str:
    nrows, ncols = grid_dimensions(grid)
    return (
        "Instruction:\n{instruction}\n\n"
        "Input grid ({rows} rows x {cols} columns):\n{grid}"
    ).format(
        instruction=instruction.strip(),
        rows=nrows,
        cols=ncols,
        grid=json.dumps(grid, ensure_ascii=False),
    )


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip())


def _parse_json_lenient(text: str) -> Any:
    """Parse the model's reply as JSON, tolerating fences and surrounding prose.

    Tries the WHOLE (fence-stripped) text first — so a valid bare array or object
    is never mangled even if a cell or the prose contains stray ``{}``/``[]``.
    Only if that fails do we try the ``{..}`` and ``[..]`` spans, accepting
    whichever actually parses. Returns the parsed value, or ``None`` if nothing
    parses.
    """
    stripped = _strip_fences(text)
    candidates = [stripped]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = stripped.find(open_ch)
        end = stripped.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append(stripped[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def parse_grid(text: str, nrows: int, ncols: int) -> Grid:
    """Parse Claude's reply into a coerced grid of EXACTLY ``nrows`` x ``ncols``.

    Raises :class:`TransformError` with an actionable message on any mismatch.
    """
    parsed = _parse_json_lenient(text)
    if parsed is None:
        raise TransformError(
            "Model did not return valid JSON. First 200 chars: %r" % (text[:200],)
        )

    if isinstance(parsed, dict):
        if "cells" not in parsed:
            raise TransformError(
                "JSON object is missing the required \"cells\" key; got keys %r"
                % (list(parsed.keys()),)
            )
        rows = parsed["cells"]
    elif isinstance(parsed, list):
        rows = parsed
    else:
        raise TransformError("Expected a JSON object or array, got %s" % type(parsed).__name__)

    if not isinstance(rows, list):
        raise TransformError("\"cells\" must be a JSON array of rows.")
    if len(rows) != nrows:
        raise TransformError(
            "Wrong number of rows: expected %d, got %d." % (nrows, len(rows))
        )

    out: Grid = []
    for r, row in enumerate(rows):
        if not isinstance(row, list):
            raise TransformError("Row %d is not a JSON array." % r)
        if len(row) != ncols:
            raise TransformError(
                "Row %d has %d columns, expected %d." % (r, len(row), ncols)
            )
        out.append([coerce_out_cell(v) for v in row])
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

# Refuse selections large enough to freeze the UI or blow the token budget.
# (A whole-column selection in Calc is ~1M rows.)
MAX_CELLS = 5000


def default_max_tokens(nrows: int, ncols: int) -> int:
    """A generous token budget scaled to the grid size (bounded)."""
    cells = max(1, nrows * ncols)
    return max(512, min(8192, cells * 48))


def transform_range(
    client: Any,
    data: Sequence[Sequence[Any]],
    instruction: str,
    *,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Grid:
    """End-to-end: read grid + instruction -> Claude -> same-shaped grid.

    ``client`` is any object exposing ``send(prompt=..., system=..., model=...,
    max_tokens=..., temperature=...)`` returning an object with a ``.text``
    attribute (i.e. :class:`claude_client.ClaudeClient`). Injecting it keeps this
    function unit-testable with a fake client.
    """
    grid = normalize_grid(data)
    nrows, ncols = grid_dimensions(grid)
    if nrows == 0 or ncols == 0:
        raise TransformError("The selection is empty; nothing to transform.")
    if nrows * ncols > MAX_CELLS:
        raise TransformError(
            "Selection is too large (%d cells; limit is %d). Select a smaller "
            "range." % (nrows * ncols, MAX_CELLS))

    result = client.send(
        system=build_system_prompt(nrows, ncols),
        prompt=build_user_prompt(grid, instruction),
        model=model,
        max_tokens=max_tokens or default_max_tokens(nrows, ncols),
        temperature=temperature,
    )
    if getattr(result, "truncated", False):
        raise TransformError(
            "Claude's answer was cut off (hit max_tokens). Select a smaller range "
            "or raise max_tokens in Claude > Settings.")
    return parse_grid(result.text, nrows, ncols)
