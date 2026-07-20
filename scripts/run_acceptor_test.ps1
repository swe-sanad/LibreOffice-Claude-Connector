# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Acceptance test for the agent-acceptor extension (docs/PLAN-PIPE-ACCEPTOR.md):
# build the .oxt, install it into an ISOLATED profile, warm-boot once (extension
# activation is next-boot), then start a NORMAL (GUI) office with NO --accept
# argument and prove it is reachable over the extension's named pipe.
#
#   powershell -ExecutionPolicy Bypass -File scripts/run_acceptor_test.ps1
#
# Why the test boot is GUI, not headless: the extension's job is to make a
# LibreOffice the user OPENED NORMALLY reachable. An empty "--headless
# --nodefault" office has no window and no accept flag, so soffice legitimately
# quits when idle (our background acceptor thread must NOT keep it alive -- the
# user's last-window-closed must still exit). So the honest test uses a real
# visible office (a window keeps it alive), exactly the production scenario.

param(
    [int]$Port = 2004,                     # warm-up boot only
    [string]$LOProgram = "C:\Program Files\LibreOffice\program"
)

$ErrorActionPreference = "Stop"
$code = 1                      # defensive: never exit 0 unless the test set it
$soffice = Join-Path $LOProgram "soffice.exe"
$py      = Join-Path $LOProgram "python.exe"
$unopkg  = Join-Path $LOProgram "unopkg.com"
foreach ($p in @($soffice, $py, $unopkg)) {
    if (-not (Test-Path $p)) { Write-Host "FAIL: not found: $p"; exit 4 }
}

$ProfileName = "lo_acceptor_profile"
$runId       = [guid]::NewGuid().ToString("N").Substring(0, 8)
$PipeName    = "lo-claude-test-$runId"
$projectRoot = Split-Path $PSScriptRoot -Parent
$profileRoot = Join-Path $env:TEMP $ProfileName
$profileDir  = Join-Path $profileRoot ("run_$runId")
$profileUrl  = "file:///" + $profileDir.Replace('\', '/')

function Kill-TestOffice {
    # marker-kill only; the fallback sweeps ONLY processes whose CommandLine is
    # unreadable (never a user office: its readable CommandLine simply lacks
    # the $ProfileName marker and is left alone).
    Get-CimInstance Win32_Process -Filter "name='soffice.bin' or name='soffice.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ProfileName*" } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
    Get-CimInstance Win32_Process -Filter "name='soffice.bin' or name='soffice.exe'" |
        Where-Object { -not $_.CommandLine } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
}

function Wait-TestOfficeGone {
    param([int]$TimeoutSec = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $left = Get-CimInstance Win32_Process -Filter "name='soffice.bin' or name='soffice.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ProfileName*" }
        if (-not $left) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Launch-Office {
    param([string[]]$AllArgs)
    try {
        $env:UserInstallation  = $profileUrl      # real isolation (see install_and_verify.ps1)
        $env:CLAUDE_AGENT_PIPE = $PipeName        # the Job honors this override
        Start-Process -FilePath $soffice -ArgumentList $AllArgs | Out-Null
    } finally {
        Remove-Item Env:\UserInstallation  -ErrorAction SilentlyContinue
        Remove-Item Env:\CLAUDE_AGENT_PIPE -ErrorAction SilentlyContinue
    }
}

function Wait-Port {
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect("localhost", $Port); $c.Close(); return $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

function Terminate-Via {
    param([string]$UnoUrl)
    $term = @"
import uno
lc = uno.getComponentContext()
r = lc.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', lc)
try:
    ctx = r.resolve('$UnoUrl')
    ctx.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', ctx).terminate()
except Exception:
    pass
"@
    $term | & $py - 2>$null
    Start-Sleep -Milliseconds 800
    Kill-TestOffice
}

# --- Build + clean slate ---
Write-Host "== Building .oxt =="
& $py (Join-Path $PSScriptRoot "build_oxt.py")
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: build"; exit 5 }
$oxt = (Get-ChildItem (Join-Path $projectRoot "dist") -Filter *.oxt |
        Sort-Object LastWriteTime | Select-Object -Last 1).FullName
Write-Host "oxt: $oxt`n"

Kill-TestOffice
Start-Sleep -Milliseconds 300
try {
    $tc = New-Object System.Net.Sockets.TcpClient
    $tc.Connect("localhost", $Port); $tc.Close()
    Write-Host "FAIL: port $Port already in use -- close it or pass -Port"; exit 5
} catch { }
if (Test-Path $profileRoot) { Remove-Item -Recurse -Force $profileRoot -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# --- Install into the isolated profile ---
Write-Host "== unopkg add =="
$env:UserInstallation = $profileUrl
& $unopkg add --suppress-license -f "-env:UserInstallation=$profileUrl" "$oxt"
if ($LASTEXITCODE -ne 0) { Remove-Item Env:\UserInstallation -EA SilentlyContinue; Write-Host "FAIL: unopkg add ($LASTEXITCODE)"; exit 6 }
Remove-Item Env:\UserInstallation -ErrorAction SilentlyContinue
Write-Host "installed OK`n"

try {
    # --- Warm-up boot (headless + socket): activates the extension next start ---
    Write-Host "== Warm-up boot =="
    Launch-Office -AllArgs @("--headless", "--norestore", "--nologo",
                             "--nofirststartwizard", "--nodefault",
                             "--accept=socket,host=localhost,port=$Port;urp;")
    if (-not (Wait-Port -TimeoutSec 240)) { Write-Host "FAIL: warm-up port never opened"; exit 3 }
    Write-Host "warm-up up; terminating to apply extension registration"
    Terminate-Via "uno:socket,host=localhost,port=$Port;urp;StarOffice.ComponentContext"
    if (-not (Wait-TestOfficeGone -TimeoutSec 40)) { Write-Host "FAIL: warm-up office never exited"; exit 3 }
    Start-Sleep -Milliseconds 1000

    # --- THE test boot: a NORMAL GUI office, NO accept argument at all ---
    # (--norestore/--nologo/--nofirststartwizard only; the Start Center window
    #  keeps it alive, exactly like a user-opened office.)
    Write-Host "`n== Test boot (GUI, no --accept) =="
    Launch-Office -AllArgs @("--norestore", "--nologo", "--nofirststartwizard")
    $env:CLAUDE_AGENT_PIPE = $PipeName
    $env:LO_PROFILE_MARKER = $ProfileName
    $env:PIPE_DEADLINE_SEC = "90"
    & $py (Join-Path $projectRoot "tests\integration\test_agent_acceptor.py")
    $code = $LASTEXITCODE
    Remove-Item Env:\CLAUDE_AGENT_PIPE, Env:\LO_PROFILE_MARKER, Env:\PIPE_DEADLINE_SEC -ErrorAction SilentlyContinue
}
finally {
    Terminate-Via "uno:pipe,name=$PipeName;urp;StarOffice.ComponentContext"
}

if ($code -eq 0) { Write-Host "`nACCEPTOR TEST: PASS" } else { Write-Host "`nACCEPTOR TEST: FAIL ($code)" }
exit $code
