# Creates the Start menu entry and the autostart entry for Shout.
#
# Shortcuts store ABSOLUTE paths, so re-run this after moving the project
# folder or the shortcuts will silently point at nothing.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1
#
# Uninstall: delete the two .lnk files this prints.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\pythonw.exe'   # pythonw: no console window
$ico = Join-Path $root 'assets\shout.ico'

if (-not (Test-Path $py)) { throw "missing interpreter: $py" }
if (-not (Test-Path $ico)) { throw "missing icon: $ico  (run scripts/make_icon.py)" }

$programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$links = @(
    (Join-Path $programs 'Shout.lnk'),
    (Join-Path $programs 'Startup\Shout.lnk')
)

$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links) {
    $sc = $shell.CreateShortcut($link)
    $sc.TargetPath = $py
    $sc.Arguments = '-m shout'
    $sc.WorkingDirectory = $root
    $sc.IconLocation = $ico
    $sc.Description = 'Shout - local dictation. Hold Ctrl+Win to talk.'
    $sc.Save()
    Write-Output "created $link"
}

Write-Output ''
Write-Output "target : $py -m shout"
Write-Output "workdir: $root"
Write-Output 'Shout will now start with Windows. A second copy exits immediately'
Write-Output 'on the single-instance mutex, so launching it twice is harmless.'
