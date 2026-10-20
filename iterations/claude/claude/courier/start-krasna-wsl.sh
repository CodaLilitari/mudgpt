#!/usr/bin/env bash
# Start Krasna's mailbox courier inside WSL (Red's own MudGPT copy).
#
#   From Windows: double-click Start-Krasna-WSL.cmd
#   From a WSL terminal:
#     bash "/mnt/c/path/to/MudGPT/.private/red/start-krasna-wsl.sh"
#
# First run creates a small Python environment in ~/.krasna (Linux side) with the
# one dependency, telnetlib3. The broker's control/lock file also lives there.
# Stop: Ctrl+C (quit in-game first; closing the socket is not an in-game save).
# Take over: bash take-over-krasna-wsl.sh   Hand back: bash release-to-red-wsl.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$HOME/.krasna"
VENV="$STATE/venv"
mkdir -p "$STATE"
chmod 700 "$STATE"

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import telnetlib3' 2>/dev/null; then
  PY=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  done
  if [ -n "$PY" ]; then
    echo "First run: creating Krasna's Python environment with $PY ..."
    "$PY" -m venv "$VENV" || { echo "The venv module is missing. Try: sudo apt install python3-venv"; exit 1; }
    "$VENV/bin/python" -m pip install --quiet telnetlib3==4.0.3
  elif command -v uv >/dev/null 2>&1; then
    echo "First run: creating Krasna's Python environment with uv ..."
    uv venv --python 3.12 "$VENV"
    uv pip install --python "$VENV/bin/python" telnetlib3==4.0.3
  else
    echo "Krasna needs Python 3.11 or newer. On Ubuntu: sudo apt install python3.12 python3.12-venv"
    exit 1
  fi
fi

export KRASNA_CONTROL="$STATE/control.json"
echo "Starting Krasna's courier. Leave this window open; Ctrl+C stops it."
cd "$HERE"
exec "$VENV/bin/python" "$HERE/mailbox_courier.py"
