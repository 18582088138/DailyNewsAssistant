# Build the exe.
#
#     powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# NOT yet executed: building and debugging the exe is a separate step by agreement.
# Read the "known pitfalls" section of docs/09_packaging.md before the first run.
#
# ASCII only, like start.bat: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI,
# so non-ASCII text here breaks on machines with a different locale.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $env:USERPROFILE "miniforge3\envs\ov_env_py312\python.exe"
$dist = Join-Path $root "dist\DailyNews"

if (-not (Test-Path $python)) {
    Write-Host "Interpreter not found: $python" -ForegroundColor Red
    exit 1
}

Push-Location $root
try {
    & $python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Install PyInstaller first: $python -m pip install pyinstaller" -ForegroundColor Red
        exit 1
    }

    Write-Host "== Building (3-8 minutes on the first run) ==" -ForegroundColor Cyan
    & $python -m PyInstaller "packaging\dna_gui.spec" --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

    # Config and prompts are copied BESIDE the exe, not bundled into it: being editable
    # is the entire point of externalising the prompts.
    Write-Host "== Copying config next to the exe ==" -ForegroundColor Cyan
    Copy-Item (Join-Path $root "config") $dist -Recurse -Force

    # .env holds REAL API keys, so it is only copied for a local build.
    # Hand out .env.example instead.
    $env_file = Join-Path $root ".env"
    if (Test-Path $env_file) {
        Copy-Item $env_file $dist -Force
        Write-Host "  Copied .env (contains real API keys - do NOT share this dist)" -ForegroundColor Yellow
    } else {
        Copy-Item (Join-Path $root ".env.example") (Join-Path $dist ".env") -Force
        Write-Host "  No .env found, copied .env.example - fill in the keys before first run" -ForegroundColor Yellow
    }

    # data/ and outputs/ are deliberately not copied: the program creates them on first
    # run, and copying them would ship the dev machine's ledger and outputs.
    Write-Host ""
    Write-Host "Done: $dist\DailyNews.exe" -ForegroundColor Green
    Write-Host "data\ and outputs\ are created automatically on first run."
}
finally {
    Pop-Location
}
