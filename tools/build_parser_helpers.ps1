param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$release = Join-Path $root 'parser\release'
$work = Join-Path $root '.tmp-pyinstaller'
$venv = Join-Path $root '.venv-parser-build'
$python = Join-Path $venv 'Scripts\python.exe'

if ($Clean -and (Test-Path -LiteralPath $release)) {
    Get-ChildItem -LiteralPath $release -File |
        Where-Object Name -ne '.gitkeep' |
        Remove-Item -Force
}
New-Item -ItemType Directory -Force -Path $release | Out-Null

if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv $venv
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to create the parser build virtual environment.'
    }
}

& $python -m pip install `
    --disable-pip-version-check `
    --no-input `
    --timeout 30 `
    --retries 1 `
    --progress-bar off `
    -r (Join-Path $root 'parser\requirements-release.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install parser build dependencies.'
}

& $python -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is unavailable in the parser build environment.'
}

foreach ($job in @(
    @{ Name = 'phireader-extract'; Script = 'parser\extract.py' },
    @{ Name = 'phireader-render'; Script = 'parser\render_page.py' }
)) {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --name $job.Name `
        --distpath $release `
        --workpath $work `
        --specpath $work `
        --collect-data pypdfium2 `
        --hidden-import fontTools.agl `
        (Join-Path $root $job.Script)
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed: $($job.Name)."
    }
}

if (Test-Path -LiteralPath $work) {
    Remove-Item -LiteralPath $work -Recurse -Force
}
Write-Output "Parser helpers generated in $release"
