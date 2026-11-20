# One-command install for Shout. From the repo folder:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Autostart
#
# 1. uv sync --locked    builds .venv from uv.lock; uv fetches Python 3.12 if needed
# 2. download_model.py   downloads the speech model and proves it transcribes
#                        correctly on this machine's GPU
# 3. download_cleanup.py downloads llama.cpp's server and the cleanup model
#                        (pinned, hash-checked) and proves one cleanup. Optional:
#                        a failure here warns and carries on
#    Steps 2 and 3 are the only network steps.
# 4. install_shortcuts   the console-free launcher and a Start menu entry, plus
#                        start-with-Windows if you pass -Autostart
#
# Safe to re-run. Re-running is also the repair after moving the folder or
# rebuilding the venv.
#
# No `$ErrorActionPreference = 'Stop'` here on purpose: uv writes its progress
# to stderr, and PowerShell 5.1 can turn a native command's stderr into a
# terminating error. Each step is checked by its exit code instead.

param([switch]$Autostart)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail([string]$message) {
    Write-Host ''
    Write-Host "Install stopped: $message" -ForegroundColor Red
    exit 1
}

function Step([string]$title) {
    Write-Host ''
    Write-Host "== $title" -ForegroundColor Cyan
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail 'uv is not installed. Install it with   winget install --id=astral-sh.uv -e   then open a NEW terminal and run this again.'
}

$gpus = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
if (-not ($gpus -match 'NVIDIA')) {
    Write-Warning "No NVIDIA GPU found (saw: $($gpus -join ', ')). Shout runs its model on an NVIDIA GPU, so step 2 will probably fail. See Requirements in the README."
}

Step '1/4  Python environment (uv sync)'
uv sync --locked
if ($LASTEXITCODE -ne 0) { Fail 'uv sync failed (see above).' }

Step '2/4  Speech model'
& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'scripts\download_model.py')
if ($LASTEXITCODE -ne 0) { Fail 'the model did not download or load (see above).' }

Step '3/4  Cleanup model'
& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'scripts\download_cleanup.py')
if ($LASTEXITCODE -ne 0) { Write-Warning 'Cleanup did not install (see above). Shout still dictates; cleanup then only removes um and uh. Re-run this script to try again.' }

Step '4/4  Launcher and shortcuts'
& (Join-Path $PSScriptRoot 'install_shortcuts.ps1') -Autostart:$Autostart

Write-Host ''
Write-Host 'Shout is installed. Start it from the Start menu, then hold Ctrl+Win and speak.' -ForegroundColor Green
