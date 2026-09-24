param(
    [string]$Python = "python",
    [string]$InnoSetup = "ISCC.exe",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail-Preflight([string]$Message) {
    throw "Windows packaging preflight failed: $Message"
}

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Fail-Preflight "Python command '$Python' was not found. Install Python 3.10+ or pass -Python <path>."
}

if (-not (Get-Command $InnoSetup -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $InnoSetup -PathType Leaf)) {
    Fail-Preflight "Inno Setup compiler '$InnoSetup' was not found. Install Inno Setup 6 or pass -InnoSetup <path-to-ISCC.exe>."
}

$requiredPaths = @(
    "config.example.yaml",
    "alembic.ini",
    "migrations",
    "src\jobcopilot\dashboard\templates",
    "scripts\hanarr_browser.py",
    "scripts\hanarr_desktop.py",
    "installer\hanarr.iss"
)
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) {
        Fail-Preflight "Required packaging input '$path' is missing."
    }
}

if ($ValidateOnly) {
    $pyinstallerVersion = & $Python -m PyInstaller --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail-Preflight "PyInstaller is unavailable for '$Python'. Install the packaging extra with '$Python -m pip install -e .[packaging]'."
    }
    Write-Host "Windows packaging preflight passed (PyInstaller $pyinstallerVersion)."
    exit 0
}

& $Python -m pip install -e ".[packaging]"
if ($LASTEXITCODE -ne 0) { throw "Could not install packaging dependencies." }

$pyinstallerVersion = & $Python -m PyInstaller --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail-Preflight "PyInstaller is unavailable after installing packaging dependencies for '$Python'."
}

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name HanarrBrowser `
    --add-data "config.example.yaml;." `
    --add-data "alembic.ini;." `
    --add-data "migrations;migrations" `
    --add-data "src\jobcopilot\dashboard\templates;jobcopilot\dashboard\templates" `
    "scripts\hanarr_browser.py"
if ($LASTEXITCODE -ne 0) { throw "Browser runtime build failed." }

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name HanarrDesktop `
    --add-data "config.example.yaml;." `
    --add-data "alembic.ini;." `
    --add-data "migrations;migrations" `
    --add-data "src\jobcopilot\dashboard\templates;jobcopilot\dashboard\templates" `
    "scripts\hanarr_desktop.py"
if ($LASTEXITCODE -ne 0) { throw "Desktop runtime build failed." }

New-Item -ItemType Directory -Force -Path "installer\output" | Out-Null
& $InnoSetup "installer\hanarr.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

$artifact = Join-Path $root "installer\output\Hanarr-Setup-0.1.0.exe"
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Inno Setup reported success but expected installer artifact '$artifact' was not created."
}
if ((Get-Item -LiteralPath $artifact).Length -le 0) {
    throw "Installer artifact '$artifact' is empty."
}

Write-Host "Unsigned installer created: $artifact"
