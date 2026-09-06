#!/usr/bin/env bash
# Start the PrismaOwl web interface (run ./install.sh first).
#   ./start.sh                        -> http://127.0.0.1:8000
#   PORT=8080 ./start.sh              -> another port
#   PRISMAOWL_NO_BROWSER=1 ./start.sh -> do not open the browser (servers, scripts)
set -euo pipefail
cd "$(dirname "$0")"
[ -x ".venv/bin/python" ] || { echo "No virtual environment found. Run ./install.sh first." >&2; exit 1; }
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
URL="http://$HOST:$PORT"
echo "PrismaOwl: $URL   (press Ctrl+C to stop)"
# Open the browser once the server answers, without blocking the server.
[ -n "${PRISMAOWL_NO_BROWSER:-}" ] || ( for _ in $(seq 1 40); do
      if curl -fsS -o /dev/null "$URL" 2>/dev/null; then
          if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1; fi
          break
      fi
      sleep 0.5
  done ) &
exec .venv/bin/python -m uvicorn app:app --host "$HOST" --port "$PORT"
