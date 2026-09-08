# Creates the Start menu entry and the autostart entry for Shout, and installs
# the console-free interpreter they point at.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1
#
# Uninstall: delete the two .lnk files this prints.
#
# Why this copies an interpreter instead of just making shortcuts
# ---------------------------------------------------------------
# uv builds a venv from small trampoline shims rather than copying the real
# interpreter, and it installs the CONSOLE trampoline under both names:
# .venv\Scripts\pythonw.exe is byte-identical to python.exe and its PE subsystem
# is 3 (console), not 2 (GUI). Measured 8 Sep 2026. Launching Shout through it
# allocated a conhost and spawned the base console python.exe as a child, so
# Windows Terminal opened a window titled "Shout" and closing that window killed
# the app.
#
# So this installs .venv\Scripts\shoutw.exe: a copy of the REAL GUI-subsystem
# pythonw.exe from the base interpreter, plus the runtime DLLs it needs beside
# it. Python locates a venv from pyvenv.cfg one directory above the executable
# and does not care what the executable is called, so shoutw.exe resolves
# sys.prefix to the venv and imports from its site-packages.
#
# A separate name rather than overwriting uv's pythonw.exe, for two reasons: the
# trampoline is locked while Shout is running, and `uv sync` would restore it
# anyway. It also makes the process legible as "shoutw.exe" in Task Manager.
#
# Shortcuts store ABSOLUTE paths, so re-run this after moving the project folder
# or the shortcuts will silently point at nothing. Re-running is also the repair
# step if a uv operation ever removes shoutw.exe.

$ErrorActionPreference = 'Stop'

function Get-PeSubsystem {
    # 2 = IMAGE_SUBSYSTEM_WINDOWS_GUI (no console), 3 = WINDOWS_CUI (console).
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
    return [BitConverter]::ToUInt16($bytes, $peOffset + 0x5c)
}

$root = Split-Path -Parent $PSScriptRoot
$scripts = Join-Path $root '.venv\Scripts'
$ico = Join-Path $root 'assets\shout.ico'
$target = Join-Path $scripts 'shoutw.exe'

if (-not (Test-Path $ico)) { throw "missing icon: $ico  (run scripts/make_icon.py)" }

# --- locate the base interpreter the venv was built from ---------------------

$cfg = Join-Path $root '.venv\pyvenv.cfg'
if (-not (Test-Path $cfg)) { throw "not a venv: $cfg is missing" }

$home_line = Select-String -Path $cfg -Pattern '^home\s*=\s*(.+)$'
if (-not $home_line) { throw "no 'home =' line in $cfg" }
$base = $home_line.Matches[0].Groups[1].Value.Trim()

$baseW = Join-Path $base 'pythonw.exe'
if (-not (Test-Path $baseW)) { throw "missing base interpreter: $baseW" }

$sub = Get-PeSubsystem $baseW
if ($sub -ne 2) {
    throw "$baseW has PE subsystem $sub, expected 2 (GUI). A console-subsystem interpreter is exactly the bug this script exists to fix."
}

# --- install the interpreter and the runtime DLLs it loads -------------------

Copy-Item $baseW $target -Force
Write-Output "installed $target  (from $baseW)"

foreach ($dll in (Get-ChildItem (Join-Path $base 'python3*.dll'))) {
    $dest = Join-Path $scripts $dll.Name
    if (-not (Test-Path $dest)) {
        Copy-Item $dll.FullName $dest
        Write-Output "installed $dest"
    }
}

# Assert the thing we actually shipped, not the thing we copied from.
$sub = Get-PeSubsystem $target
if ($sub -ne 2) { throw "$target has PE subsystem $sub, expected 2 (GUI)" }

# A GUI-subsystem exe has no stdout to capture, so the check has to come back
# through a file. Capturing its (always empty) pipeline output would "pass" by
# measuring nothing.
$probe = Join-Path ([System.IO.Path]::GetTempPath()) 'shout_prefix_probe.txt'
Remove-Item $probe -ErrorAction SilentlyContinue
& $target -c "import sys, faster_whisper; open(r'$probe','w').write(sys.prefix)" | Out-Null
if (-not (Test-Path $probe)) { throw "$target produced no output - it failed to start or could not import faster_whisper" }
$prefix = (Get-Content $probe -Raw).Trim()
Remove-Item $probe -ErrorAction SilentlyContinue

$expected = (Resolve-Path (Join-Path $root '.venv')).Path
if ($prefix -ne $expected) { throw "shoutw.exe resolved sys.prefix to '$prefix', expected '$expected'" }
Write-Output "verified  subsystem=2 (no console)  sys.prefix=$prefix"

# --- shortcuts ---------------------------------------------------------------

$programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$links = @(
    (Join-Path $programs 'Shout.lnk'),
    (Join-Path $programs 'Startup\Shout.lnk')
)

$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links) {
    $sc = $shell.CreateShortcut($link)
    $sc.TargetPath = $target
    $sc.Arguments = '-m shout'
    $sc.WorkingDirectory = $root
    $sc.IconLocation = $ico
    $sc.Description = 'Shout - local dictation. Hold Ctrl+Win to talk.'
    $sc.Save()
    Write-Output "created $link"
}

Write-Output ''
Write-Output "target : $target -m shout"
Write-Output "workdir: $root"
Write-Output 'Shout will now start with Windows, with no console window. A second'
Write-Output 'copy exits immediately on the single-instance mutex, so launching it'
Write-Output 'twice is harmless.'
