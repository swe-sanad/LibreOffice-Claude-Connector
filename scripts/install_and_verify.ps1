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
# Fresh profile dir per run (see run_integration.ps1: stale locks from a killed
# run make LibreOffice self-relaunch WITHOUT -env:UserInstallation).
$profileRoot = Join-Path $env:TEMP $ProfileName
$profileDir  = Join-Path $profileRoot ("run_{0}" -f ([guid]::NewGuid().ToString("N").Substring(0, 8)))
$profileUrl  = "file:///" + $profileDir.Replace('\', '/')
$accept = "socket,host=localhost,port=$Port;urp;"
# NOTE: profile isolation is via the UserInstallation ENV VAR (set in
# Launch-And-Wait), NOT `-env:UserInstallation=` — that switch is silently
# dropped by the launcher on some Windows builds, booting the user's REAL
# profile instead. See run_integration.ps1 for the full explanation.
$soArgs = @("--headless", "--norestore", "--nologo", "--nofirststartwizard",
            "--nodefault", "--accept=$accept")

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
    $env:UserInstallation = $profileUrl   # real isolation (see $soArgs note)
    Start-Process -FilePath $soffice -ArgumentList $soArgs | Out-Null
    Remove-Item Env:\UserInstallation -ErrorAction SilentlyContinue
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

function Test-OwnInstance {
    # True only if the office answering the socket uses OUR isolated profile.
    # Windows single-instance handling can delegate a launch to an already
    # running office (which then opens the socket) — never treat that one as
    # ours: we must not verify against, let alone terminate, a user session.
    $verify = @"
import sys, time, uno
lc = uno.getComponentContext()
r = lc.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', lc)
deadline = time.time() + 40
last = None
while time.time() < deadline:
    try:
        ctx = r.resolve('uno:socket,host=localhost,port=$Port;urp;StarOffice.ComponentContext')
        ps = ctx.ServiceManager.createInstanceWithContext('com.sun.star.util.PathSettings', ctx)
        sys.exit(0 if '$ProfileName' in ps.UserConfig else 7)
    except SystemExit:
        raise
    except Exception as exc:   # URP bridge not ready yet on a cold boot
        last = exc
        time.sleep(1)
sys.stderr.write('verify: bridge never became ready: %s' % last)
sys.exit(8)
"@
    $verify | & $py -
    return ($LASTEXITCODE -eq 0)
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
# HARD STOP if the port is occupied by some OTHER office (e.g. a GUI LibreOffice
# with a UNO socket) — proceeding would verify against, and later try to
# TERMINATE, the user's real session.
try {
    $tc = New-Object System.Net.Sockets.TcpClient
    $tc.Connect("localhost", $Port); $tc.Close()
    Write-Host "FAIL: port $Port is already in use by another process. Close it"
    Write-Host "      or re-run with -Port <free port>."
    exit 5
} catch { }
if (Test-Path $profileRoot) { Remove-Item -Recurse -Force $profileRoot -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# --- Install (office not running) ---
# Belt AND suspenders: pass BOTH the -env: switch (unopkg.com honors it) and the
# UserInstallation env var (what soffice.exe honors), so the extension lands in
# the SAME isolated profile the test office will boot into. Without this the
# extension would install into the user's REAL profile.
Write-Host "== unopkg add =="
$env:UserInstallation = $profileUrl
& $unopkg add --suppress-license -f "-env:UserInstallation=$profileUrl" "$oxt"
if ($LASTEXITCODE -ne 0) { Remove-Item Env:\UserInstallation -EA SilentlyContinue; Write-Host "FAIL: unopkg add ($LASTEXITCODE)"; exit 6 }
& $unopkg list "-env:UserInstallation=$profileUrl" | Out-Null
Remove-Item Env:\UserInstallation -ErrorAction SilentlyContinue
Write-Host "installed OK`n"

$foreign = $false
try {
    # --- Warm-up boot: activates the extension's handler/menu config ---
    Write-Host "== Warm-up boot (activate extension) =="
    if (-not (Launch-And-Wait -TimeoutSec 240)) { Write-Host "FAIL: warm-up port never opened"; exit 3 }
    if (-not (Test-OwnInstance)) {
        $foreign = $true
        Write-Host "FAIL: the office on port $Port is NOT the isolated test instance."
        Write-Host "      Close all LibreOffice windows and re-run."
        exit 6
    }
    Write-Host "warm-up up; terminating to apply registration"
    Terminate-Office
    if (-not (Wait-PortClosed -TimeoutSec 40)) { Write-Host "FAIL: warm-up port stayed open"; exit 3 }
    Start-Sleep -Milliseconds 1000

    # --- Test boot: now the ProtocolHandler is active ---
    Write-Host "`n== Test boot =="
    if (-not (Launch-And-Wait -TimeoutSec 180)) { Write-Host "FAIL: test port never opened"; exit 3 }
    if (-not (Test-OwnInstance)) {
        $foreign = $true
        Write-Host "FAIL: the office on port $Port is NOT the isolated test instance."
        Write-Host "      Close all LibreOffice windows and re-run."
        exit 6
    }
    Write-Host "up; running dispatch verification`n"
    $env:LO_UNO_PORT = "$Port"
    & $py (Join-Path $projectRoot "tests\integration\test_extension_dispatch.py")
    $code = $LASTEXITCODE
}
finally {
    # Never send terminate at a foreign (user's) office.
    if (-not $foreign) { Terminate-Office }
}

exit $code
