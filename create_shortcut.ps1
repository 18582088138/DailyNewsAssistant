# Put a "DailyNews Workbench" shortcut on the desktop. Run once.
#
# ASCII only, on purpose: Windows PowerShell 5.1 reads a .ps1 without a BOM as ANSI,
# so non-ASCII text in the script turns into mojibake or a parse error depending on
# the machine's locale.
#
# Points at start.bat rather than duplicating the launch command -- a copy would have
# to be edited in two places, and nobody remembers the shortcut.
#
# Usage:
#     right-click -> Run with PowerShell
#     or: powershell -ExecutionPolicy Bypass -File create_shortcut.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $root "start.bat"

if (-not (Test-Path $target)) {
    Write-Host "Not found: $target -- keep this script next to start.bat." -ForegroundColor Red
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$link = Join-Path $desktop "DailyNews Workbench.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
# The working directory must be the repo root: config/, data/ and outputs/ all resolve
# relative to it. Without this the shortcut starts in C:\Windows\System32, finds no
# config, and comes up with an empty database -- no error, just nothing there.
$shortcut.WorkingDirectory = $root
$shortcut.Description = "DailyNewsAssistant article workbench"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$shortcut.Save()

Write-Host "Created: $link" -ForegroundColor Green
Write-Host "Double-click it to start the workbench (the browser opens automatically)."
