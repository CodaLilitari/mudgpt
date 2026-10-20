#!/usr/bin/env bash
# Take control of Krasna's session from Red (when the courier runs in WSL).
# Red's commands are refused until you run: bash release-to-red-wsl.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$HOME/.krasna"
PYTHONPATH="$HERE/src" "$STATE/venv/bin/python" -m mudgpt --control "$STATE/control.json" claim human --takeover
echo
echo "You hold control. To type a game command yourself:"
echo "  PYTHONPATH=\"$HERE/src\" $STATE/venv/bin/python -m mudgpt --control $STATE/control.json send \"look\" --view plain"
