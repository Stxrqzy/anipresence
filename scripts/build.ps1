# Build the PyInstaller exe, then the Inno Setup installer, keeping the
# version in sync: the single source of truth is src/__init__.py, which is
# injected into ISCC as /DMyAppVersion (the .iss only hardcodes a fallback).
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$match = Select-String -Path "src\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $match) { throw "Could not find __version__ in src\__init__.py" }
$version = $match.Matches[0].Groups[1].Value
Write-Host ">> Building version $version"

python -m PyInstaller --noconfirm AniPresence.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

# Runtime artifacts written by a test run must not be packaged.
Remove-Item -LiteralPath "dist\AniPresence\extension\settings.json" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "dist\AniPresence\_internal\extension\settings.json" -ErrorAction SilentlyContinue

$iscc = $env:ISCC
if (-not $iscc) { $iscc = "C:\Users\tnahe\AppData\Local\Programs\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path -LiteralPath $iscc)) { throw "ISCC.exe not found at $iscc" }

& $iscc "/DMyAppVersion=$version" AniPresence.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }
$installer = "installer\AniPresence-Setup-$version.exe"
$feed = @{
    version = $version
    url = "https://github.com/Stxrqzy/anipresence-releases/releases/download/v$version/AniPresence-Setup-$version.exe"
    sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $repo "installer\update.json"), $feed)
Write-Host ">> Done: installer\AniPresence-Setup-$version.exe"
