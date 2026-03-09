# Fix Docker 500 Internal Server Error
# Chay trong PowerShell

Write-Host "=== Fix Docker 500 Error ===" -ForegroundColor Cyan

# 1. Dat API version cu hon (workaround)
$env:DOCKER_API_VERSION = "1.41"
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;$env:PATH"

# 2. Thu docker version
Write-Host "`n[1] Kiem tra Docker..." -ForegroundColor Yellow
$result = docker version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Docker OK!" -ForegroundColor Green
    Write-Host "`nChay docker compose voi:" -ForegroundColor Green
    Write-Host '  $env:DOCKER_API_VERSION="1.41"; docker compose -f docker-compose.dev.yml up --build' -ForegroundColor White
} else {
    Write-Host "  Docker chua san sang." -ForegroundColor Red
    Write-Host "`n[2] Thu Reset Docker Desktop:" -ForegroundColor Yellow
    Write-Host "  1. Mo Docker Desktop" -ForegroundColor White
    Write-Host "  2. Settings (gear) > Troubleshoot > Reset to factory defaults" -ForegroundColor White
    Write-Host "  3. Doi 2-3 phut cho Docker khoi dong lai" -ForegroundColor White
    Write-Host "  4. Chay lai: docker compose -f docker-compose.dev.yml up --build" -ForegroundColor White
}
