#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Sanad Arousi
//
// Node.js launcher for the LibreOffice MCP server.
//
// The server itself is Python — it MUST run under LibreOffice's bundled Python,
// because that interpreter is the only one that ships the `uno` module (the
// LibreOffice API bridge). No Node or PyPI package can provide `uno`, so this
// launcher's whole job is to find that interpreter and hand it the server with
// stdio passed straight through (the MCP JSON-RPC stream flows untouched).

"use strict";
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

function findLibreOfficePython() {
  const candidates = [
    process.env.LIBREOFFICE_PYTHON, // set from manifest user_config
    // Windows
    "C:\\Program Files\\LibreOffice\\program\\python.exe",
    "C:\\Program Files (x86)\\LibreOffice\\program\\python.exe",
    // macOS
    "/Applications/LibreOffice.app/Contents/Resources/python",
    // Linux (python3-uno / libreoffice-script-provider-python installed)
    "/usr/bin/python3",
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      if (fs.existsSync(c)) return c;
    } catch (_) {
      /* keep looking */
    }
  }
  return null;
}

const py = findLibreOfficePython();
if (!py) {
  console.error(
    "[libreoffice-connector] Could not find LibreOffice's bundled Python. " +
      "Install LibreOffice (https://libreoffice.org) or set the " +
      "'LibreOffice bundled Python' path in the extension settings."
  );
  process.exit(1);
}

// bundle layout: index.js sits beside mcp/; repo layout: index.js is in mcpb/
const server = [
  path.join(__dirname, "mcp", "libreoffice_mcp.py"),
  path.join(__dirname, "..", "mcp", "libreoffice_mcp.py"),
].find((p) => fs.existsSync(p));
if (!server) {
  console.error("[libreoffice-connector] server script not found next to launcher");
  process.exit(1);
}

console.error("[libreoffice-connector] launching: " + py + " " + server);

// EXPLICIT piping, not stdio:"inherit" — inherited raw handles do not survive
// the Electron -> Node -> Python grandchild chain on Windows (the Python server
// sees a closed stdin and exits immediately; Claude Desktop logs a transport
// close right after `initialize`). Piping through this process is the reliable
// path, and -u disables Python's block buffering on the piped stdout.
const child = spawn(py, ["-u", server], {
  stdio: ["pipe", "pipe", "pipe"],
  env: process.env,
  windowsHide: true,
});
process.stdin.pipe(child.stdin);
child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);
child.on("error", (err) => {
  console.error("[libreoffice-connector] failed to start server: " + err.message);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  console.error(
    "[libreoffice-connector] server exited code=" + code + " signal=" + signal
  );
  process.exit(signal ? 1 : code == null ? 1 : code);
});
process.stdin.on("end", () => child.stdin.end());
