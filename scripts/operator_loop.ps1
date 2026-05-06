param(
    [int]$IntervalSeconds = 180,
    [switch]$SkipLive
)

$ErrorActionPreference = "Continue"

$root = (Resolve-Path "$PSScriptRoot\..").Path
$watchdog = Join-Path $root "scripts\\watchdog.ps1"
$ui = Join-Path $root "scripts\\ui_smoke.py"
$pythonExe = Join-Path $root ".venv\\Scripts\\python.exe"

if (-not (Test-Path $watchdog)) { throw "Missing watchdog: $watchdog" }
if (-not (Test-Path $ui)) { throw "Missing ui smoke: $ui" }
if (-not (Test-Path $pythonExe)) { throw "Missing python: $pythonExe" }

Write-Host ""
Write-Host "Operator loop running." -ForegroundColor Yellow
Write-Host "Interval: $IntervalSeconds seconds"
Write-Host "Local Flask: http://127.0.0.1:8080/health"
Write-Host "Command Center: http://127.0.0.1:3021/command-center"
Write-Host ""

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host ""
    Write-Host "[$ts] Running watchdog + UI smoke..." -ForegroundColor Cyan

    try {
        if ($SkipLive) {
            powershell -ExecutionPolicy Bypass -File $watchdog -SkipLive | Out-Host
        } else {
            powershell -ExecutionPolicy Bypass -File $watchdog | Out-Host
        }
    } catch {
        Write-Host "Watchdog failed; continuing to UI smoke anyway." -ForegroundColor Red
    }

    try {
        & $pythonExe $ui | Out-Host
    } catch {
        Write-Host "UI smoke crashed: $($_.Exception.Message)" -ForegroundColor Red
    }

    Start-Sleep -Seconds $IntervalSeconds
}

