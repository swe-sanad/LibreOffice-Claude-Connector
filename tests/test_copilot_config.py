# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Regression tests for repo-native MCP support in VS Code / GitHub Copilot."""

import json
import pathlib
import unittest


class CopilotMcpConfigTest(unittest.TestCase):
    def test_workspace_mcp_config_registers_libreoffice_for_vs_code(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        config_path = root / ".vscode" / "mcp.json"
        self.assertTrue(config_path.exists(), "expected workspace MCP config")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers = data.get("servers")
        self.assertIsInstance(servers, dict)

        server = servers.get("libreoffice")
        self.assertIsInstance(server, dict)
        self.assertEqual(server.get("type"), "stdio")
        self.assertEqual(server.get("command"), "node")
        self.assertIn("${workspaceFolder}/mcpb/index.js", server.get("args", []))


if __name__ == "__main__":
    unittest.main()
