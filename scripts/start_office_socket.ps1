# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Start YOUR normal LibreOffice (GUI, real profile) with a UNO socket open, so
# the LibreOffice MCP server (mcp/libreoffice_mcp.py) — and thus Claude Code —
# can reach into whatever document you have open.
#
#   powershell -ExecutionPolicy Bypass -File scripts/start_office_socket.ps1
#
# IMPORTANT: LibreOffice is single-instance. If it is already running, a second
# launch just focuses the existing window and does NOT open the socket. Fully
# close LibreOffice first (including the system-tray Quickstarter), then run this.

param(
    [int]$Port = 2002,
    [string]$LOProgram = "C:\Program Files\LibreOffice\program"
)

$soffice = Join-Path $LOProgram "soffice.exe"
if (-not (Test-Path $soffice)) { Write-Host "FAIL: not found: $soffice"; exit 4 }

$gui = Get-Process -Name soffice, soffice.bin -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 }
if ($gui) {
    Write-Host "NOTE: LibreOffice appears to already be running."
    Write-Host "      Fully close it (and the tray Quickstarter), then re-run this"
    Write-Host "      so the UNO socket on port $Port actually opens."
}

Write-Host "Starting LibreOffice with a UNO socket on localhost:$Port ..."
Start-Process -FilePath $soffice -ArgumentList @("--accept=socket,host=localhost,port=$Port;urp;")
Write-Host "Done. The MCP server (LO_UNO_PORT=$Port) can now connect."
Write-Host "See mcp/README.md to register it with Claude Code."
