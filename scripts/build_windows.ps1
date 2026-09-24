param(
    [string]$Python = "python",
    [string]$InnoSetup = "ISCC.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& $Python -m pip install -e ".[packaging]"
if ($LASTEXITCODE -ne 0) { throw "Could not install packaging dependencies." }

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

Write-Host "Unsigned installer created under installer\output."
