# Starts Noor Guide and a temporary HTTPS tunnel so a phone or tablet can open
# the guest screen with camera and microphone access.
# Usage:  powershell -ExecutionPolicy Bypass -File share.ps1 [-Port 8000]
# Anyone with the printed link can open the app while this window is running.

param([int]$Port = 8000)

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared is not installed. Run: winget install --id Cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}

$env:HF_HUB_OFFLINE = "1"   # models are already cached; never re-download mid-demo
$server = Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", $Port `
    -WorkingDirectory $root -PassThru -NoNewWindow

try {
    Write-Host "Server starting on http://localhost:$Port (guide console)" -ForegroundColor Green
    Write-Host "Waiting for the tunnel address..." -ForegroundColor Green
    cloudflared tunnel --no-autoupdate --url "http://localhost:$Port" 2>&1 | ForEach-Object {
        $line = "$_"
        if ($line -match "https://[a-z0-9-]+\.trycloudflare\.com") {
            $url = $Matches[0]
            Write-Host ""
            Write-Host "  Guide console : http://localhost:$Port" -ForegroundColor Cyan
            Write-Host "  Guest screen  : $url/guest" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "Press Ctrl+C to stop the tunnel and the server." -ForegroundColor Yellow
        }
    }
} finally {
    Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
}
