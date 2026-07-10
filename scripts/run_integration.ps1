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
# Each run gets a FRESH profile dir under the marker root: a stale .lock or
# half-deleted profile from a previous (killed) run makes LibreOffice fail its
# boot and self-relaunch WITHOUT -env:UserInstallation — i.e. as a real-profile
# office squatting on our port. A unique dir per run sidesteps that entirely.
$ProfileName = "lo_uno_it_profile"
$profileRoot = Join-Path $env:TEMP $ProfileName
$profileDir  = Join-Path $profileRoot ("run_{0}" -f ([guid]::NewGuid().ToString("N").Substring(0, 8)))
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
# Wait for the UNO port to be free — a prior back-to-back run's instance may still
# be closing, and connecting to it gives "URP bridge disposed".
$portFree = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $portFree) {
    try { $tc = New-Object System.Net.Sockets.TcpClient; $tc.Connect("localhost", $Port); $tc.Close(); Start-Sleep -Milliseconds 500 }
    catch { break }
}
# HARD STOP if the port is still occupied: it belongs to some OTHER office (e.g.
# a GUI LibreOffice started with start_office_socket.ps1). Proceeding would run
# the test against — and afterwards try to TERMINATE — the user's real session.
try {
    $tc = New-Object System.Net.Sockets.TcpClient
    $tc.Connect("localhost", $Port); $tc.Close()
    Write-Host "FAIL: port $Port is already in use by another process (a GUI"
    Write-Host "      LibreOffice with a UNO socket?). Close it or re-run with"
    Write-Host "      -Port <free port>."
    exit 5
} catch { }
# Best-effort cleanup of previous runs' profiles (locked leftovers are fine —
# this run uses its own fresh subdirectory).
if (Test-Path $profileRoot) { Remove-Item -Recurse -Force $profileRoot -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$accept = "socket,host=localhost,port=$Port;urp;"
$soArgs = @("--headless", "--norestore", "--nologo", "--nofirststartwizard",
            "--nodefault", "--accept=$accept")

# Isolate the profile via the UserInstallation ENVIRONMENT VARIABLE, not the
# `-env:UserInstallation=` command-line switch. On some Windows LibreOffice
# builds the launcher silently drops that switch (verified: the office comes up
# on the user's REAL profile, so the test — and the teardown terminate() — would
# hit the user's session). The env var is honored reliably. The child inherits
# our env at spawn time, so we clear it again right after launching.
Write-Host "Launching isolated LibreOffice on UNO port $Port ..."
$env:UserInstallation = $profileUrl
Start-Process -FilePath $soffice -ArgumentList $soArgs | Out-Null
Remove-Item Env:\UserInstallation -ErrorAction SilentlyContinue

$foreign = $false
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

    # IDENTITY CHECK: make sure the office answering the socket really is OUR
    # isolated instance (its user profile contains our marker). Windows'
    # single-instance handling can delegate a launch to an already-running
    # office, which then opens the socket — running the test against (and
    # afterwards terminating!) the user's real session.
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
    if ($LASTEXITCODE -ne 0) {
        $foreign = $true
        Write-Host "FAIL: could not confirm the office on port $Port is the"
        Write-Host "      isolated test instance (exit $LASTEXITCODE; 7 = foreign"
        Write-Host "      office, 8 = bridge never answered). Refusing to touch"
        Write-Host "      it. Close all LibreOffice windows and re-run."
        exit 6
    }
    Write-Host "UNO port open (isolated instance verified); running $Test`n"

    $env:LO_UNO_PORT = "$Port"
    & $py $Test
    $code = $LASTEXITCODE
}
finally {
    # Never send terminate at a foreign (user's) office.
    if (-not $foreign) {
        Stop-Office -Port $Port -Py $py -Marker $ProfileName
    }
}

exit $code
