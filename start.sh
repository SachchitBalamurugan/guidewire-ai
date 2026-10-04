#!/usr/bin/env bash
# Guidewire: one-command start for macOS and Linux.
# First run: sets everything up (Python, packages, AI models). Needs internet once.
# Later runs: start straight away, fully offline.
#
#   bash start.sh
#
# Optional settings (environment variables):
#   GUIDEWIRE_LLM=ollama|rules   skip the question about the 3B suggestion model
#   GUIDEWIRE_PORT=8000          port to use
#   GUIDEWIRE_NO_BROWSER=1       don't open the browser
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PORT="${GUIDEWIRE_PORT:-8000}"
PY="$ROOT/.venv/bin/python"
STATE="$ROOT/.guidewire-setup"

step() { printf '\n\033[32m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$1"; exit 1; }

printf '\033[36mGuidewire: a tour co-pilot for small tourism operators\033[0m\n'

# ---------- 1. uv (installs Python for us) ----------
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  UV="$ROOT/.tools/uv"
  if [ ! -x "$UV" ]; then
    step "Installing uv (a small tool that sets up Python). One time only."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$ROOT/.tools" UV_NO_MODIFY_PATH=1 sh \
      || fail "Could not install uv. Check the internet connection and try again."
  fi
fi

# ---------- 2. Python + packages ----------
if [ ! -x "$PY" ]; then
  step "Setting up Python 3.12 and the app's packages. One time only, a few minutes."
  "$UV" venv --python 3.12 .venv || fail "Could not create the Python environment."
fi
if ! "$PY" -c "import fastapi, faster_whisper, ctranslate2, sentencepiece" 2>/dev/null; then
  "$UV" pip install --python "$PY" -r requirements.txt || fail "Could not install the packages. Check the internet connection and try again."
fi

# ---------- 3. AI models (speech + translation) ----------
if [ ! -f "$ROOT/.models-ready" ]; then
  step "Downloading the speech and translation models (about 1.1 GB). One time only."
  "$PY" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); from huggingface_hub import snapshot_download; snapshot_download('JustFrederik/nllb-200-distilled-600M-ct2-int8'); print('Models ready.')" \
    || fail "Could not download the models. Check the internet connection and try again."
  touch "$ROOT/.models-ready"
fi

# ---------- 4. Optional: the 3B suggestion model ----------
LLM="${GUIDEWIRE_LLM:-}"
[ -z "$LLM" ] && [ -f "$STATE" ] && LLM="$(tr -d '[:space:]' < "$STATE")"
if [ -z "$LLM" ]; then
  printf '\n\033[33mOptional: also set up the 3B suggestion model (Qwen2.5 via Ollama, about 2 GB)?\033[0m\n'
  echo "Without it, suggestions come from the built-in rules. Everything else still uses the AI models."
  read -r -p "Set it up? [y/N] " ANSWER || ANSWER=""
  case "$ANSWER" in [yY]|[yY][eE][sS]) LLM=ollama ;; *) LLM=rules ;; esac
fi
if [ "$LLM" = "ollama" ]; then
  if ! command -v ollama >/dev/null 2>&1; then
    step "Installing Ollama. One time only."
    if [ "$(uname)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then brew install ollama || true
    elif [ "$(uname)" = "Linux" ]; then curl -fsSL https://ollama.com/install.sh | sh || true
    fi
  fi
  if command -v ollama >/dev/null 2>&1; then
    curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || { (ollama serve >/dev/null 2>&1 &); sleep 4; }
    if ! ollama list 2>/dev/null | grep -q "qwen2.5:3b"; then
      step "Downloading the 3B suggestion model (about 2 GB). One time only."
      ollama pull qwen2.5:3b || { echo "Model download failed; using the built-in rules."; LLM=rules; }
    fi
  else
    echo "Couldn't install Ollama automatically. Get it from https://ollama.com/download and run start again."
    echo "Starting with the built-in rules for now."
    LLM=rules
  fi
fi
echo "$LLM" > "$STATE"

# ---------- 5. Start ----------
export LLM_BACKEND="$LLM" HF_HUB_OFFLINE=1 PRELOAD_MODELS=1
step "Starting Guidewire on http://localhost:$PORT (works offline from now on)"
"$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null || true' EXIT INT TERM
for _ in $(seq 1 90); do
  sleep 1
  kill -0 "$SERVER" 2>/dev/null || fail "The app stopped while starting. See the messages above."
  curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
done
curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 || fail "The app didn't start within 90 seconds."
printf '\n\033[36m  Noor'"'"'s screen (guide) : http://localhost:%s\n  Guest'"'"'s screen        : http://localhost:%s/guest\n  Results               : http://localhost:%s/insights\033[0m\n\n' "$PORT" "$PORT" "$PORT"
echo "Keep this window open while you use it. Press Ctrl+C to stop."
if [ -z "${GUIDEWIRE_NO_BROWSER:-}" ]; then
  if command -v open >/dev/null 2>&1; then open "http://localhost:$PORT"; sleep 1; open "http://localhost:$PORT/guest"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$PORT" >/dev/null 2>&1; sleep 1; xdg-open "http://localhost:$PORT/guest" >/dev/null 2>&1
  fi
fi
wait "$SERVER"
