"""Unit tests for TODO-01 through TODO-09 (RTL/CTL, formula normalization,
document_lifecycle no_document, dimensions, page setup, table presets)."""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))

from loconn.core import _normalize_formula  # noqa: E402
from loconn.tools import calc_format, calc_sheets, shared_properties, writer_tables  # noqa: E402


def test_normalize_formula_skips_plain_text_commas():
    """TODO-08: plain text cells (not starting with '=') must not have commas replaced."""
    assert _normalize_formula("1,500 س1 ← 3,500 س2", ";") == "1,500 س1 ← 3,500 س2"
    assert _normalize_formula("717,899 د.ل", ";") == "717,899 د.ل"


def test_normalize_formula_skips_quoted_literals_and_normalizes_args():
    """TODO-08: commas inside '...' string literals stay, top-level args become ';'."""
    assert _normalize_formula('=IF(A1>0, "1,500", "2,000")', ";") == '=IF(A1>0; "1,500"; "2,000")'


def test_normalize_formula_strips_odf_brackets():
    """TODO-07: accidental ODF [...] brackets around sheet refs are stripped."""
    assert _normalize_formula("=['Sheet Name'.G5]+1", ";") == "='Sheet Name'.G5+1"
    assert _normalize_formula("=SUM([Sheet1.A1]:[Sheet1.A5])", ";") == "=SUM(Sheet1.A1:Sheet1.A5)"


def test_document_lifecycle_no_document_advisory():
    """TODO-04: document_lifecycle returns advisory payload when no doc is open."""
    with patch.object(shared_properties, "_bridge", return_value=object()), \
         patch.object(shared_properties, "_select_doc", return_value=None), \
         patch.object(shared_properties, "_current_doc",
                      side_effect=RuntimeError("[no_document] No document is open.")):
        res = shared_properties.tool_document_lifecycle({})
    assert res["phase"] == "no_document"
    tools = [a["tool"] for a in res["next_actions"]]
    assert "create_document" in tools
    assert "open_document" in tools


def _tool_schema(module, name):
    for t in module.TOOL_DEFS:
        if t["name"] == name:
            return t["inputSchema"]["properties"]
    raise KeyError(name)


def test_schemas_expose_new_parameters():
    """Verify schemas for TODO-02, TODO-03, TODO-05, TODO-06, TODO-09."""
    fmt_props = _tool_schema(calc_format, "calc_format_range")
    for k in ("writing_mode", "padding_mm", "padding_v_mm", "padding_h_mm"):
        assert k in fmt_props

    tbl_props = _tool_schema(calc_format, "calc_format_table")
    for k in ("header_bg", "header_fg", "zebra_bg", "even_row_bg", "odd_row_bg", "border_color"):
        assert k in tbl_props
    assert "neutral" in tbl_props["preset"]["enum"]
    assert "sanad" in tbl_props["preset"]["enum"]

    wtbl_props = _tool_schema(writer_tables, "writer_format_table")
    for k in ("zebra_bg", "even_row_bg", "odd_row_bg", "header_bg", "header_fg"):
        assert k in wtbl_props

    dim_props = _tool_schema(calc_sheets, "calc_set_dimensions")
    assert "sizes_mm" in dim_props

    ps_props = _tool_schema(calc_sheets, "calc_page_setup")
    assert "header_on" in ps_props
    assert "footer_on" in ps_props
