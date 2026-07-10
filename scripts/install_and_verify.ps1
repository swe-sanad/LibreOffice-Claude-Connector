# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Build the .oxt, install it into an ISOLATED LibreOffice profile (so it never
# touches your real office), then verify the extension's component loads and
# registers its dispatch handler. Tears down after itself.
#
# Note: LibreOffice activates a newly-installed extension's UI/handler config on
# the NEXT start after install (the usual "restart to apply"). So we boot once to
# activate, then boot again to run the check.
#
#   powershell -ExecutionPolicy Bypass -File scripts/install_and_verify.ps1

param(
    [int]$Port = 2003,
    [string]$LOProgram = "C:\Program Files\LibreOffice\program"
)

$ErrorActionPreference = "Stop"
$soffice = Join-Path $LOProgram "soffice.exe"
$py      = Join-Path $LOProgram "python.exe"
$unopkg  = Join-Path $LOProgram "unopkg.com"
foreach ($p in @($soffice, $py, $unopkg)) {
    if (-not (Test-Path $p)) { Write-Host "FAIL: not found: $p"; exit 4 }
}

$ProfileName = "lo_ext_profile"
$projectRoot = Split-Path $PSScriptRoot -Parent
$profileDir  = Join-Path $env:TEMP $ProfileName
$profileUrl  = "file:///" + $profileDir.Replace('\', '/')
$accept = "socket,host=localhost,port=$Port;urp;"
$soArgs = @("--headless", "--norestore", "--nologo", "--nofirststartwizard",
            "--nodefault", "--accept=$accept", "-env:UserInstallation=$profileUrl")

function Kill-TestOffice {
    Get-CimInstance Win32_Process -Filter "name='soffice.bin' or name='soffice.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ProfileName*" } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
}

function Terminate-Office {
    $term = @"
import uno
lc = uno.getComponentContext()
r = lc.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', lc)
try:
    ctx = r.resolve('uno:socket,host=localhost,port=$Port;urp;StarOffice.ComponentContext')
    ctx.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', ctx).terminate()
except Exception: pass
"@
    $term | & $py - 2>$null
    Start-Sleep -Milliseconds 800
    Kill-TestOffice
    # Fallback: a still-running HEADLESS soffice is a leftover test instance
    # (a real GUI office has a non-zero MainWindowHandle), and the marker-kill
    # can miss instances whose CommandLine is unreadable via CIM.
    Get-Process -Name soffice, soffice.bin -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -eq 0 } |
        ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {} }
}

function Launch-And-Wait {
    param([int]$TimeoutSec)
    Start-Process -FilePath $soffice -ArgumentList $soArgs | Out-Null
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect("localhost", $Port); $c.Close(); return $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

function Wait-PortClosed {
    param([int]$TimeoutSec = 40)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect("localhost", $Port); $c.Close(); Start-Sleep -Milliseconds 500 }
        catch { return $true }   # connect failed => port is closed
    }
    return $false
}

# --- Build ---
Write-Host "== Building .oxt =="
& $py (Join-Path $PSScriptRoot "build_oxt.py")
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: build"; exit 5 }
$oxt = (Get-ChildItem (Join-Path $projectRoot "dist") -Filter *.oxt |
        Sort-Object LastWriteTime | Select-Object -Last 1).FullName
Write-Host "oxt: $oxt`n"

# --- Clean slate ---
Kill-TestOffice
Start-Sleep -Milliseconds 300
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# --- Install (office not running) ---
Write-Host "== unopkg add =="
& $unopkg add --suppress-license -f "-env:UserInstallation=$profileUrl" "$oxt"
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: unopkg add ($LASTEXITCODE)"; exit 6 }
& $unopkg list "-env:UserInstallation=$profileUrl" | Out-Null
Write-Host "installed OK`n"

try {
    # --- Warm-up boot: activates the extension's handler/menu config ---
    Write-Host "== Warm-up boot (activate extension) =="
    if (-not (Launch-And-Wait -TimeoutSec 240)) { Write-Host "FAIL: warm-up port never opened"; exit 3 }
    Write-Host "warm-up up; terminating to apply registration"
    Terminate-Office
    if (-not (Wait-PortClosed -TimeoutSec 40)) { Write-Host "FAIL: warm-up port stayed open"; exit 3 }
    Start-Sleep -Milliseconds 1000

    # --- Test boot: now the ProtocolHandler is active ---
    Write-Host "`n== Test boot =="
    if (-not (Launch-And-Wait -TimeoutSec 180)) { Write-Host "FAIL: test port never opened"; exit 3 }
    Write-Host "up; running dispatch verification`n"
    $env:LO_UNO_PORT = "$Port"
    & $py (Join-Path $projectRoot "tests\integration\test_extension_dispatch.py")
    $code = $LASTEXITCODE
}
finally {
    Terminate-Office
}

exit $code
