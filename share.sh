#!/usr/bin/env bash
# Starts Guidewire and a temporary HTTPS tunnel so phones and tablets can open it
# with microphone and camera access (browsers only allow those over HTTPS).
# macOS / Linux counterpart of share.ps1.
# Usage:  ./share.sh [port]      (default 8000)
# Anyone with the printed link can open the app while this is running.

set -euo pipefail
PORT="${1:-8000}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed. Run: brew install cloudflared  (Linux: see developers.cloudflare.com)" >&2
  exit 1
fi
if [ ! -x "$PYTHON" ]; then
  echo "No virtualenv found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export HF_HUB_OFFLINE=1   # models are already cached; never re-download mid-demo
cd "$ROOT"
"$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null || true' EXIT INT TERM

echo "Server starting on http://localhost:$PORT"
echo "Waiting for the tunnel address..."
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" 2>&1 | while IFS= read -r line; do
  if [[ "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
    url="${BASH_REMATCH[1]}"
    echo
    echo "  Guide console (phone or laptop) : $url"
    echo "  Guest screen  (guests' phones)  : $url/guest"
    echo "  On this laptop                  : http://localhost:$PORT"
    echo
    echo "Press Ctrl+C to stop the tunnel and the server."
  fi
done
