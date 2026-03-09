# Sửa Docker Desktop chạy trên Hyper-V
# Chạy PowerShell AS ADMINISTRATOR

Write-Host "=== Sửa Docker Desktop + Hyper-V ===" -ForegroundColor Cyan

# 1. Bật đủ Windows Features
Write-Host "`n[1] Bật Windows Features..." -ForegroundColor Yellow
$features = @("Microsoft-Hyper-V", "Containers")
foreach ($f in $features) {
    try {
        Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart | Out-Null
        Write-Host "  $f : OK" -ForegroundColor Green
    } catch {
        Write-Host "  $f : $($_.Exception.Message)" -ForegroundColor Gray
    }
}

# 2. Đặt Docker service = Automatic
Write-Host "`n[2] Cấu hình Docker service..." -ForegroundColor Yellow
Set-Service -Name "com.docker.service" -StartupType Automatic -ErrorAction SilentlyContinue
Write-Host "  StartupType = Automatic" -ForegroundColor Green

# 3. Đảm bảo settings dùng Hyper-V
$settingsPath = "$env:APPDATA\Docker\settings-store.json"
Write-Host "`n[3] Cập nhật settings (wslEngineEnabled = false)..." -ForegroundColor Yellow
$settingsDir = Split-Path $settingsPath -Parent
if (-not (Test-Path $settingsDir)) { New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null }
$json = if (Test-Path $settingsPath) { Get-Content $settingsPath -Raw } else { "{}" }
$obj = $json | ConvertFrom-Json
$obj | Add-Member -NotePropertyName "wslEngineEnabled" -NotePropertyValue $false -Force
$obj | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
Write-Host "  OK" -ForegroundColor Green

# 4. Đóng Docker Desktop (để restart sạch)
Write-Host "`n[4] Đóng Docker Desktop..." -ForegroundColor Yellow
Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "  OK" -ForegroundColor Green

Write-Host "`n=== Các bước tiếp theo ===" -ForegroundColor Green
Write-Host "1. Khởi động lại máy (restart) - quan trọng!" -ForegroundColor White
Write-Host "2. Mở Docker Desktop từ Start Menu" -ForegroundColor White
Write-Host "3. Nếu vẫn lỗi: Docker Desktop > Settings > Troubleshoot > Reset to factory defaults" -ForegroundColor White
Write-Host "   (Sẽ xóa containers/images, tạo lại VM Hyper-V mới)" -ForegroundColor Gray
