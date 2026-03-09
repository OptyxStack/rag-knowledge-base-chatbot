# Uninstall WSL - Run PowerShell AS ADMINISTRATOR
# Docker uses Hyper-V (wslEngineEnabled: false)

Write-Host "=== Uninstall WSL ===" -ForegroundColor Cyan

# 1. List and unregister Linux distros
Write-Host "`n[1] WSL distributions..." -ForegroundColor Yellow
$distros = wsl --list --quiet 2>$null
if ($distros) {
    foreach ($d in $distros) {
        if ($d -and $d -ne "") {
            Write-Host "  Unregister: $d"
            wsl --unregister $d 2>$null
        }
    }
} else {
    Write-Host "  No distros found." -ForegroundColor Gray
}

# 2. wsl --uninstall
Write-Host "`n[2] wsl --uninstall..." -ForegroundColor Yellow
wsl --uninstall 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  wsl --uninstall not available, trying Disable-WindowsOptionalFeature..." -ForegroundColor Gray
}

# 3. Disable Windows Features (skip if component store corrupted)
Write-Host "`n[3] Disable Windows Features (WSL, VirtualMachinePlatform)..." -ForegroundColor Yellow
try {
    Disable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -ErrorAction Stop
    Write-Host "  Microsoft-Windows-Subsystem-Linux: disabled" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Message -match "corrupted") {
        Write-Host "`n  Component store corrupted. Run repair first:" -ForegroundColor Yellow
        Write-Host "  .\scripts\repair-windows-component-store.ps1" -ForegroundColor White
        Write-Host "  Then restart and run this script again." -ForegroundColor Gray
    }
}
try {
    Disable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -ErrorAction Stop
    Write-Host "  VirtualMachinePlatform: disabled" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Message -match "corrupted") {
        Write-Host "`n  Component store corrupted. Run repair first:" -ForegroundColor Yellow
        Write-Host "  .\scripts\repair-windows-component-store.ps1" -ForegroundColor White
    }
}

Write-Host "`n=== Done. Restart to apply. ===" -ForegroundColor Green
Write-Host "Docker will use Hyper-V after restart." -ForegroundColor Gray
