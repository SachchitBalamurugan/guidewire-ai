# Guidewire: one-command start for Windows.
# First run: sets everything up (Python, packages, AI models). Needs internet once.
# Later runs: start straight away, fully offline.
#
#   Double-click start.bat, or run:  powershell -ExecutionPolicy Bypass -File start.ps1
#
# Optional settings (environment variables):
#   GUIDEWIRE_LLM=ollama|rules   skip the question about the 3B suggestion model
#   GUIDEWIRE_PORT=8000          port to use
#   GUIDEWIRE_NO_BROWSER=1       don't open the browser

# Native tools (uv, python, ollama) report progress on stderr; Windows PowerShell 5.1
# would treat that as fatal under "Stop", so check exit codes instead.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Set-Location $root
$port = if ($env:GUIDEWIRE_PORT) { $env:GUIDEWIRE_PORT } else { "8000" }
$python = Join-Path $root ".venv\Scripts\python.exe"
$stateFile = Join-Path $root ".guidewire-setup"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Green }
function Fail($text) { Write-Host ""; Write-Host $text -ForegroundColor Red; exit 1 }

Write-Host "Guidewire: a tour co-pilot for small tourism operators" -ForegroundColor Cyan

# ---------- 1. uv (installs Python for us) ----------
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Join-Path $root ".tools\uv.exe"
    if (-not (Test-Path $uv)) {
        Step "Installing uv (a small tool that sets up Python). One time only."
        $env:UV_INSTALL_DIR = Join-Path $root ".tools"
        $env:UV_NO_MODIFY_PATH = "1"
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        if (-not (Test-Path $uv)) { Fail "Could not install uv. Check the internet connection and try again." }
    }
}

# ---------- 2. Python + packages ----------
if (-not (Test-Path $python)) {
    Step "Setting up Python 3.12 and the app's packages. One time only, a few minutes."
    & $uv venv --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment." }
}
& $python -c "import fastapi, faster_whisper, ctranslate2, sentencepiece" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $uv pip install --python $python -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "Could not install the packages. Check the internet connection and try again." }
}

# ---------- 3. AI models (speech + translation) ----------
$modelsReady = Join-Path $root ".models-ready"
if (-not (Test-Path $modelsReady)) {
    Step "Downloading the speech and translation models (about 1.1 GB). One time only."
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
    & $python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); from huggingface_hub import snapshot_download; snapshot_download('JustFrederik/nllb-200-distilled-600M-ct2-int8'); print('Models ready.')"
    if ($LASTEXITCODE -ne 0) { Fail "Could not download the models. Check the internet connection and try again." }
    New-Item -ItemType File -Path $modelsReady -Force | Out-Null
}

# ---------- 4. Optional: the 3B suggestion model ----------
$llm = $env:GUIDEWIRE_LLM
if (-not $llm -and (Test-Path $stateFile)) { $llm = (Get-Content $stateFile -Raw).Trim() }
if (-not $llm) {
    Write-Host ""
    Write-Host "Optional: also set up the 3B suggestion model (Qwen2.5 via Ollama, about 2 GB)?" -ForegroundColor Yellow
    Write-Host "Without it, suggestions come from the built-in rules. Everything else still uses the AI models."
    $answer = Read-Host "Set it up? [y/N]"
    $llm = if ($answer -match '^(y|yes)$') { "ollama" } else { "rules" }
}
if ($llm -eq "ollama") {
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollama) {
        $default = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $default) { $ollama = $default }
    }
    if (-not $ollama -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Step "Installing Ollama. One time only."
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements --silent
        $default = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $default) { $ollama = $default }
    }
    if (-not $ollama) {
        Write-Host "Couldn't install Ollama automatically. Get it from https://ollama.com/download and run start again." -ForegroundColor Yellow
        Write-Host "Starting with the built-in rules for now."
        $llm = "rules"
    } else {
        # make sure the Ollama service is up, then fetch the model once
        try { Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 2 -ErrorAction Stop | Out-Null }
        catch { Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 4 }
        $have = & $ollama list 2>$null | Select-String "qwen2.5:3b"
        if (-not $have) {
            Step "Downloading the 3B suggestion model (about 2 GB). One time only."
            & $ollama pull qwen2.5:3b
            if ($LASTEXITCODE -ne 0) { Write-Host "Model download failed; using the built-in rules." -ForegroundColor Yellow; $llm = "rules" }
        }
    }
}
Set-Content -Path $stateFile -Value $llm

# ---------- 5. Start ----------
$env:LLM_BACKEND = $llm
$env:HF_HUB_OFFLINE = "1"
$env:PRELOAD_MODELS = "1"
Step "Starting Guidewire on http://localhost:$port (works offline from now on)"
$server = Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", $port -NoNewWindow -PassThru
try {
    $up = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        if ($server.HasExited) { Fail "The app stopped while starting. See the messages above." }
        try { Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 2 -ErrorAction Stop | Out-Null; $up = $true; break } catch {}
    }
    if (-not $up) { Fail "The app didn't start within 90 seconds." }
    Write-Host ""
    Write-Host "  Noor's screen (guide) : http://localhost:$port" -ForegroundColor Cyan
    Write-Host "  Guest's screen        : http://localhost:$port/guest" -ForegroundColor Cyan
    Write-Host "  Results               : http://localhost:$port/insights" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Keep this window open while you use it. Press Ctrl+C to stop." -ForegroundColor Yellow
    if (-not $env:GUIDEWIRE_NO_BROWSER) {
        Start-Process "http://localhost:$port"
        Start-Sleep -Milliseconds 800
        Start-Process "http://localhost:$port/guest"
    }
    Wait-Process -Id $server.Id
} finally {
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
}
