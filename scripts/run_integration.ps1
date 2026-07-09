# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Launch an ISOLATED headless LibreOffice (its own user profile, so it does not
# disturb any LibreOffice you have open), run a UNO integration test against it,
# then shut that instance down. Exits with the test's exit code.
#
#   powershell -ExecutionPolicy Bypass -File scripts/run_integration.ps1
#   powershell ... -File scripts/run_integration.ps1 -Test tests/integration/test_writer_uno.py

param(
    [string]$Test = "tests/integration/test_calc_uno.py",
    [int]$Port = 2002,
    [string]$LOProgram = "C:\Program Files\LibreOffice\program"
)

$ErrorActionPreference = "Stop"
$soffice = Join-Path $LOProgram "soffice.exe"
$py      = Join-Path $LOProgram "python.exe"

foreach ($p in @($soffice, $py)) {
    if (-not (Test-Path $p)) { Write-Host "FAIL: not found: $p"; exit 4 }
}

# Fresh, isolated profile so we never collide with the user's running office.
$profileDir = Join-Path $env:TEMP "lo_uno_it_profile"
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
$profileUrl = "file:///" + ($profileDir -replace '\\', '/')

$accept  = "socket,host=localhost,port=$Port;urp;"
$soArgs  = @("--headless", "--norestore", "--nologo", "--nofirststartwizard",
             "--nodefault", "--accept=$accept", "-env:UserInstallation=$profileUrl")

Write-Host "Launching isolated LibreOffice on UNO port $Port ..."
$proc = Start-Process -FilePath $soffice -ArgumentList $soArgs -PassThru

function Stop-Office {
    param([int]$Port, [string]$Py, $Proc)
    $term = @"
import uno
lc = uno.getComponentContext()
r = lc.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', lc)
try:
    ctx = r.resolve('uno:socket,host=localhost,port=$Port;urp;StarOffice.ComponentContext')
    ctx.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', ctx).terminate()
except Exception:
    pass
"@
    $term | & $Py - 2>$null
    if ($Proc -and -not $Proc.HasExited) {
        try { $Proc.Kill() } catch {}
    }
}

try {
    # Wait (check-first) for the UNO socket to open.
    $deadline = (Get-Date).AddSeconds(90)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect("localhost", $Port); $c.Close(); $ready = $true; break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { Write-Host "FAIL: UNO port $Port never opened"; exit 3 }
    Write-Host "UNO port open; running $Test`n"

    $env:LO_UNO_PORT = "$Port"
    & $py $Test
    $code = $LASTEXITCODE
}
finally {
    Stop-Office -Port $Port -Py $py -Proc $proc
}

exit $code
