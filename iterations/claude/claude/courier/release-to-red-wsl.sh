#!/usr/bin/env bash
# Hand Krasna's session back to Red after a takeover (courier running in WSL).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$HOME/.krasna"
PYTHONPATH="$HERE/src" "$STATE/venv/bin/python" -m mudgpt --control "$STATE/control.json" release human
echo "Released. Red can claim control again."
