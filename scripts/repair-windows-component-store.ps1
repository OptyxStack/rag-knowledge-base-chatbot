# Sua Windows Component Store (loi "The component store has been corrupted")
# Chay PowerShell AS ADMINISTRATOR
# Mat 15-30 phut

$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Sua Windows Component Store ===" -ForegroundColor Cyan
Write-Host "Can 15-30 phut. Khong tat cua so." -ForegroundColor Yellow

# 1. Kiem tra
Write-Host "`n[1] Kiem tra Component Store..." -ForegroundColor Yellow
Dism /Online /Cleanup-Image /CheckHealth
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Phat hien van de. Tiep tuc scan..." -ForegroundColor Gray
}

# 2. Scan
Write-Host "`n[2] Scan Component Store..." -ForegroundColor Yellow
Dism /Online /Cleanup-Image /ScanHealth

# 3. SFC (System File Checker)
Write-Host "`n[3] Chay SFC /scannow..." -ForegroundColor Yellow
sfc /scannow

# 4. DISM RestoreHealth
Write-Host "`n[4] DISM RestoreHealth..." -ForegroundColor Yellow
Dism /Online /Cleanup-Image /RestoreHealth

Write-Host "`n=== Xong ===" -ForegroundColor Green
Write-Host "Restart may, sau do chay lai uninstall-wsl.ps1 neu can go WSL." -ForegroundColor Gray
