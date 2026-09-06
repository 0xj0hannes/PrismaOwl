# PrismaOwl installer for Windows (PowerShell).
#   From a clone:  .\install.ps1
# Checks for Python 3.9+, creates the virtual environment (.venv), installs the
# dependencies and prepares the data directory. Start the app with .\start.ps1
$ErrorActionPreference = "Stop"
$MinVersion = [version]"3.9"

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path "app.py")) {
    if (Test-Path (Join-Path $PSScriptRoot "app.py")) { Set-Location $PSScriptRoot }
    else {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git is required to download PrismaOwl (https://git-scm.com)." }
        if (-not (Test-Path "PrismaOwl")) { Say "Downloading PrismaOwl"; git clone --quiet https://github.com/0xj0hannes/PrismaOwl.git PrismaOwl }
        Set-Location "PrismaOwl"
    }
}

# Find Python 3.9+ (the "py" launcher first, then python).
$py = $null
foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
    $exe = $candidate[0]; $args = $candidate[1..($candidate.Length)]
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        try {
            $v = & $exe @args -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and ([version]$v -ge $MinVersion)) { $py = @($exe) + $args; break }
        } catch {}
    }
}
if (-not $py) { Fail "Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this script again." }
Say "Using Python $v"

if (-not (Test-Path ".venv\Scripts\python.exe")) { Say "Creating the virtual environment (.venv)"; & $py[0] $py[1..($py.Length)] -m venv .venv }
Say "Installing dependencies"
& ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

New-Item -ItemType Directory -Force -Path data, logs | Out-Null
if (-not (Test-Path ".env")) { "# PrismaOwl settings - managed by the Settings dialog in the web interface" | Set-Content ".env" }

Say "PrismaOwl is installed in $(Get-Location)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Start the app:      .\start.ps1"
Write-Host "  2. Open the browser:   http://127.0.0.1:8000"
Write-Host "  3. Click 'Settings' (bottom left) and enter your OrcaRouter or Gemini API key."
