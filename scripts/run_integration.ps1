# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Launch an ISOLATED headless LibreOffice (its own user profile, so it never
# disturbs any LibreOffice you have open), run a UNO integration test against it,
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

# A marker unique to OUR test instances. Killing by this marker can NEVER touch
# a normal LibreOffice (which runs with the default user profile).
$ProfileName = "lo_uno_it_profile"
$profileDir  = Join-Path $env:TEMP $ProfileName
$profileUrl  = "file:///" + ($profileDir -replace '\\', '/')

function Kill-TestOffice {
    param([string]$Marker)
    Get-CimInstance Win32_Process -Filter "name='soffice.bin' or name='soffice.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Marker*" } |
        ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
}

function Stop-Office {
    param([int]$Port, [string]$Py, [string]$Marker)
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
    Start-Sleep -Milliseconds 600
    Kill-TestOffice -Marker $Marker   # hard-stop anything still alive
}

# --- Clean slate: kill any leftover test instance, then reset the profile ---
Kill-TestOffice -Marker $ProfileName
Start-Sleep -Milliseconds 300
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$accept = "socket,host=localhost,port=$Port;urp;"
$soArgs = @("--headless", "--norestore", "--nologo", "--nofirststartwizard",
            "--nodefault", "--accept=$accept", "-env:UserInstallation=$profileUrl")

Write-Host "Launching isolated LibreOffice on UNO port $Port ..."
Start-Process -FilePath $soffice -ArgumentList $soArgs | Out-Null

try {
    # Wait (check-first) for the UNO socket. A first-run cold profile can be slow.
    $deadline = (Get-Date).AddSeconds(150)
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
    Stop-Office -Port $Port -Py $py -Marker $ProfileName
}

exit $code
