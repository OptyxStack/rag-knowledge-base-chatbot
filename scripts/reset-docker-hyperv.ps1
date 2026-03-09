# Fix Docker 500 Internal Server Error - Reset Docker Desktop
# Sau khi xoa WSL data, Docker daemon bi loi. Can reset de tao lai VM Hyper-V.

Write-Host "=== Fix Docker 500 Error ===" -ForegroundColor Cyan

# 1. Dong Docker
Write-Host "`n[1] Dong Docker Desktop..." -ForegroundColor Yellow
Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# 2. Huong dan Reset
Write-Host "`n[2] RESET DOCKER (bat buoc):" -ForegroundColor Yellow
Write-Host "  1. Mo Docker Desktop" -ForegroundColor White
Write-Host "  2. Settings (gear) > Troubleshoot > Reset to factory defaults" -ForegroundColor White
Write-Host "  3. Confirm" -ForegroundColor White
Write-Host "  4. Doi 2-3 phut" -ForegroundColor White
Write-Host "`n   Sau khi reset, settings wslEngineEnabled=false van duoc giu." -ForegroundColor Gray

# 3. Mo Docker
Write-Host "`n[3] Mo Docker Desktop..." -ForegroundColor Yellow
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
