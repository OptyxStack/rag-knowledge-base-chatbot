# Chuyen Docker sang Hyper-V (khong can Admin)
# Chi cap nhat settings va khoi dong lai Docker Desktop

$settingsPath = "$env:APPDATA\Docker\settings-store.json"
$settingsDir = Split-Path $settingsPath -Parent

Write-Host "=== Chuyen Docker sang Hyper-V ===" -ForegroundColor Cyan

# 1. Dong Docker Desktop
Write-Host "`n[1] Dong Docker Desktop..." -ForegroundColor Yellow
Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Write-Host "  OK" -ForegroundColor Green

# 2. Cap nhat settings
Write-Host "`n[2] Dat wslEngineEnabled = false (dung Hyper-V)..." -ForegroundColor Yellow
if (-not (Test-Path $settingsDir)) { New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null }
$json = if (Test-Path $settingsPath) { Get-Content $settingsPath -Raw } else { "{}" }
$obj = $json | ConvertFrom-Json
$obj | Add-Member -NotePropertyName "wslEngineEnabled" -NotePropertyValue $false -Force
$obj | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
Write-Host "  OK" -ForegroundColor Green

# 3. Mo Docker Desktop
Write-Host "`n[3] Mo Docker Desktop..." -ForegroundColor Yellow
$ddPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $ddPath) {
    Start-Process $ddPath
    Write-Host "  Docker Desktop dang khoi dong (cho 30-60 giay)..." -ForegroundColor Green
} else {
    Write-Host "  Khong tim thay Docker Desktop. Mo thu cong tu Start Menu." -ForegroundColor Yellow
}

Write-Host "`n=== Xong ===" -ForegroundColor Green
Write-Host "Docker se chay tren Hyper-V." -ForegroundColor Gray
