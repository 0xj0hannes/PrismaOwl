#!/usr/bin/env bash
# PrismaOwl installer for macOS / Linux.
#
#   From a clone:      ./install.sh
#   Without a clone:   curl -fsSL https://raw.githubusercontent.com/0xj0hannes/PrismaOwl/main/install.sh | bash
#
# Checks for Python 3.9+, creates the virtual environment (.venv), installs the
# dependencies and prepares the data directory. Nothing is installed outside
# the PrismaOwl folder. Start the app afterwards with ./start.sh
set -euo pipefail

REPO_URL="https://github.com/0xj0hannes/PrismaOwl.git"
MIN_MAJOR=3
MIN_MINOR=9

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Locate the project (clone it if this script runs standalone).
if [ -f "app.py" ] && [ -f "requirements.txt" ]; then
    PROJECT_DIR="$(pwd)"
elif [ -f "$(dirname "$0")/app.py" ]; then
    PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
else
    command -v git >/dev/null 2>&1 || fail "git is required to download PrismaOwl (https://git-scm.com)."
    if [ -d "PrismaOwl" ]; then
        say "Using the existing PrismaOwl folder in $(pwd)"
    else
        say "Downloading PrismaOwl into $(pwd)/PrismaOwl"
        git clone --quiet "$REPO_URL" PrismaOwl
    fi
    PROJECT_DIR="$(cd PrismaOwl && pwd)"
fi
cd "$PROJECT_DIR"

# 2. Find a suitable Python.
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null; then
            PY="$candidate"; break
        fi
    fi
done
[ -n "$PY" ] || fail "Python $MIN_MAJOR.$MIN_MINOR or newer was not found. Install it from https://www.python.org/downloads/ and run this script again."
say "Using $($PY --version 2>&1) at $(command -v "$PY")"

# 3. Virtual environment + dependencies.
if [ ! -x ".venv/bin/python" ]; then
    say "Creating the virtual environment (.venv)"
    "$PY" -m venv .venv
fi
say "Installing dependencies"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# 4. Data directory and settings file.
mkdir -p data logs
if [ ! -f ".env" ]; then
    # The Settings dialog in the app fills this in; an empty file is enough.
    printf '# PrismaOwl settings - managed by the Settings dialog in the web interface\n' > .env
fi
chmod +x start.sh 2>/dev/null || true

say "PrismaOwl is installed in $PROJECT_DIR"
cat <<MSG

Next steps:
  1. Start the app:      cd "$PROJECT_DIR" && ./start.sh
  2. Open the browser:   http://127.0.0.1:8000
  3. Click "Settings" (bottom left) and enter your OrcaRouter or Gemini API key.
MSG
