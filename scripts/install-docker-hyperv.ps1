# Cài Docker Desktop với Hyper-V backend (không dùng WSL)
# Chạy PowerShell AS ADMINISTRATOR

$ErrorActionPreference = "Stop"
$InstallerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
$InstallerPath = "$env:TEMP\DockerDesktopInstaller.exe"

Write-Host "=== Cài Docker Desktop với Hyper-V ===" -ForegroundColor Cyan

# 1. Bật Hyper-V và Containers
Write-Host "`n[1] Bật Windows Features (Hyper-V, Containers)..." -ForegroundColor Yellow
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All -NoRestart | Out-Null
Enable-WindowsOptionalFeature -Online -FeatureName Containers -All -NoRestart | Out-Null
Write-Host "  OK" -ForegroundColor Green

# 2. Cập nhật settings để dùng Hyper-V (trước khi cài hoặc sau)
$settingsPath = "$env:APPDATA\Docker\settings-store.json"
$settingsDir = Split-Path $settingsPath -Parent
if (-not (Test-Path $settingsDir)) { New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null }

# 3. Tải installer
Write-Host "`n[3] Tải Docker Desktop Installer..." -ForegroundColor Yellow
Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath -UseBasicParsing
Write-Host "  OK" -ForegroundColor Green

# 4. Cài với backend Hyper-V
Write-Host "`n[4] Cài Docker Desktop (--backend=hyper-v)..." -ForegroundColor Yellow
$proc = Start-Process -FilePath $InstallerPath -ArgumentList "install", "--backend=hyper-v", "--accept-license", "--quiet" -PassThru -Wait
if ($proc.ExitCode -ne 0) {
    Write-Host "  Exit code: $($proc.ExitCode). Thử cài không quiet để xem lỗi." -ForegroundColor Red
    Start-Process -FilePath $InstallerPath -ArgumentList "install", "--backend=hyper-v", "--accept-license" -Wait
}

# 5. Đảm bảo settings dùng Hyper-V (wslEngineEnabled = false)
Write-Host "`n[5] Cấu hình Docker dùng Hyper-V..." -ForegroundColor Yellow
$json = if (Test-Path $settingsPath) { Get-Content $settingsPath -Raw } else { "{}" }
$obj = $json | ConvertFrom-Json
$obj | Add-Member -NotePropertyName "wslEngineEnabled" -NotePropertyValue $false -Force
$obj | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
Write-Host "  wslEngineEnabled = false" -ForegroundColor Green

Write-Host "`n=== Xong ===" -ForegroundColor Green
Write-Host "Khởi động Docker Desktop từ Start Menu." -ForegroundColor Gray
Write-Host "Docker sẽ chạy trên Hyper-V (không cần WSL)." -ForegroundColor Gray
