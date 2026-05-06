param(
    [switch]$SkipLive,
    [switch]$SkipWriteTest
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Test-Http([string]$Url, [int]$TimeoutSec = 8) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return @{ ok = $true; status = [int]$resp.StatusCode; body = $resp.Content }
    } catch {
        return @{ ok = $false; status = 0; body = $_.Exception.Message }
    }
}

function Start-Flask([string]$Root, [string]$PythonExe) {
    Write-Step "Starting Flask backend on 8080"
    Start-Process -FilePath $PythonExe -ArgumentList "server.py" -WorkingDirectory $Root | Out-Null
    Start-Sleep -Seconds 3
}

function Start-Reflex([string]$Root, [string]$ReflexExe) {
    Write-Step "Starting Reflex Command Center on 3020/3021"
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-Command",
        "`$env:REFLEX_BACKEND_PORT='3020'; `$env:REFLEX_FRONTEND_PORT='3021'; `$env:REFLEX_API_URL='http://127.0.0.1:3020'; `$env:REFLEX_DEPLOY_URL='http://127.0.0.1:3021'; & `"$ReflexExe`" run"
    ) -WorkingDirectory $Root | Out-Null
    Start-Sleep -Seconds 10
}

function Test-Db([string]$PythonExe, [string]$Root) {
    $cmd = "from models import get_session; from sqlalchemy import text; s=get_session(); print(s.execute(text('select 1')).scalar()); s.close()"
    try {
        $out = & $PythonExe -c $cmd 2>&1
        return ($out -match "1")
    } catch {
        return $false
    }
}

function Run-ContactWriteTest([string]$PythonExe, [string]$Root) {
    if ($SkipWriteTest) { return $true }
    $cmd = @"
import requests
from models import get_session, ContactSubmission
from sqlalchemy import desc
r = requests.post('http://127.0.0.1:8080/contact', data={
  'name':'Watchdog Test',
  'email':'watchdog.test+local@autoyieldsystems.com',
  'company':'AutoYield QA',
  'message':'Watchdog smoke test submission.'
}, timeout=15, allow_redirects=False)
ok_redirect = (r.status_code == 302 and '/contact?sent=1' in (r.headers.get('Location') or ''))
s = get_session()
row = s.query(ContactSubmission).order_by(desc(ContactSubmission.id)).first()
s.close()
ok_row = bool(row and row.name == 'Watchdog Test' and row.source == 'public_site')
print('ok' if (ok_redirect and ok_row) else 'fail')
"@
    try {
        $out = & $PythonExe -c $cmd 2>&1
        return ($out -match "ok")
    } catch {
        return $false
    }
}

$root = (Resolve-Path "$PSScriptRoot\..").Path
$pythonExe = Join-Path $root ".venv\Scripts\python.exe"
$reflexExe = Join-Path $root ".venv\Scripts\reflex.exe"

if (-not (Test-Path $pythonExe)) { throw "Missing python at $pythonExe" }
if (-not (Test-Path $reflexExe)) { throw "Missing reflex at $reflexExe" }

Write-Step "Checking local backend"
$localHealth = Test-Http "http://127.0.0.1:8080/health"
if (-not $localHealth.ok) {
    Start-Flask -Root $root -PythonExe $pythonExe
    $localHealth = Test-Http "http://127.0.0.1:8080/health"
}

Write-Step "Checking Command Center"
$cc = Test-Http "http://127.0.0.1:3021/command-center"
if (-not $cc.ok) {
    Start-Reflex -Root $root -ReflexExe $reflexExe
    $cc = Test-Http "http://127.0.0.1:3021/command-center" -TimeoutSec 12
}

Write-Step "Checking database connection"
$dbOk = Test-Db -PythonExe $pythonExe -Root $root

Write-Step "Checking contact write path"
$writeOk = Run-ContactWriteTest -PythonExe $pythonExe -Root $root

$liveHealth = @{ ok = $true; status = 0; body = "skipped" }
if (-not $SkipLive) {
    Write-Step "Checking live production health"
    $liveHealth = Test-Http "https://autoyieldsystems.com/health" -TimeoutSec 12
}

$allOk = $localHealth.ok -and $cc.ok -and $dbOk -and $writeOk -and $liveHealth.ok

Write-Host ""
Write-Host "================ WATCHDOG REPORT ================" -ForegroundColor Yellow
Write-Host ("Local Flask health:      " + ($(if ($localHealth.ok) { "OK" } else { "FAIL" })))
Write-Host ("Command Center route:    " + ($(if ($cc.ok) { "OK" } else { "FAIL" })))
Write-Host ("Database query:          " + ($(if ($dbOk) { "OK" } else { "FAIL" })))
Write-Host ("Contact write test:      " + ($(if ($writeOk) { "OK" } else { "FAIL" })))
Write-Host ("Live prod health:        " + ($(if ($liveHealth.ok) { "OK" } else { "FAIL" })))
Write-Host "================================================="

if (-not $allOk) {
    if (-not $localHealth.ok) { Write-Host "Local Flask error: $($localHealth.body)" -ForegroundColor Red }
    if (-not $cc.ok) { Write-Host "Command Center error: $($cc.body)" -ForegroundColor Red }
    if (-not $liveHealth.ok) { Write-Host "Live health error: $($liveHealth.body)" -ForegroundColor Red }
    throw "Watchdog failed. One or more checks did not pass."
}

Write-Host "Watchdog passed. Stack is healthy." -ForegroundColor Green
