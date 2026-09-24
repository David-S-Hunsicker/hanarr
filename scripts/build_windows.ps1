param(
    [string]$Python = "python",
    [string]$InnoSetup = "ISCC.exe",
    [string]$Metadata = "packaging\release-metadata.json",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail-Preflight([string]$Message) {
    throw "Windows packaging preflight failed: $Message"
}

if (-not (Test-Path -LiteralPath $Metadata -PathType Leaf)) {
    Fail-Preflight "Release metadata '$Metadata' was not found."
}
$releaseMetadata = Get-Content -LiteralPath $Metadata -Raw | ConvertFrom-Json
if ($releaseMetadata.product -ne "Hanarr" -or $releaseMetadata.platform -ne "windows") {
    Fail-Preflight "Release metadata must describe the Hanarr Windows package."
}
if ($releaseMetadata.version -notmatch '^\d+\.\d+\.\d+$') {
    Fail-Preflight "Release metadata version '$($releaseMetadata.version)' is not a stable x.y.z version."
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
    "installer\hanarr.iss",
    $Metadata
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
& $InnoSetup "/DMyAppVersion=$($releaseMetadata.version)" "installer\hanarr.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

$artifact = Join-Path $root "installer\output\Hanarr-Setup-$($releaseMetadata.version).exe"
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Inno Setup reported success but expected installer artifact '$artifact' was not created."
}
if ((Get-Item -LiteralPath $artifact).Length -le 0) {
    throw "Installer artifact '$artifact' is empty."
}

$hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
$hashFile = [System.IO.Path]::ChangeExtension($artifact, ".sha256")
"$hash  $([System.IO.Path]::GetFileName($artifact))" | Set-Content -LiteralPath $hashFile -Encoding ascii

$outputMetadata = [ordered]@{
    schema_version = $releaseMetadata.schema_version
    product = $releaseMetadata.product
    version = $releaseMetadata.version
    platform = $releaseMetadata.platform
    artifact = [System.IO.Path]::GetFileName($artifact)
    sha256 = $hash
    signing = $releaseMetadata.signing
    notarization = $releaseMetadata.notarization
    build_status = "unsigned-success"
}
$outputMetadata | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root "installer\output\release-metadata.json") -Encoding utf8

Write-Host "Unsigned installer created: $artifact"
Write-Host "SHA-256: $hash"
