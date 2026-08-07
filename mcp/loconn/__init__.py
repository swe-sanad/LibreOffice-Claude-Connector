# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""LibreOffice MCP server internals.

    registry.py   the tool registry — one declaration site per tool
    core.py       shared UNO machinery: connection, documents, undo, errors
    tools/        one module per application per concern; importing the
                  package registers every tool

The protocol layer lives in ``mcp/libreoffice_mcp.py``.
"""
