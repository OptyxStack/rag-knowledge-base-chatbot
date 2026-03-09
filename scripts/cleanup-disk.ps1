# Giai phong dung luong o dia - Chay PowerShell
# Doc docs/DISK_USAGE_REPORT.md truoc khi chay

$freed = 0

Write-Host "=== Giai phong dung luong o dia ===" -ForegroundColor Cyan

# 1. Xoa file tam (an toan)
Write-Host "`n[1] Xoa file tam (Temp)..." -ForegroundColor Yellow
$tempSize = (Get-ChildItem $env:TEMP -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
Remove-Item "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  Giai phong: $([math]::Round($tempSize/1GB,2)) GB" -ForegroundColor Green

# 2. Docker prune (can Docker dang chay)
Write-Host "`n[2] Docker system prune..." -ForegroundColor Yellow
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;$env:PATH"
$dockerPrune = docker system prune -a -f 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Docker prune OK" -ForegroundColor Green
} else {
    Write-Host "  Docker chua chay hoac loi. Bo qua." -ForegroundColor Gray
}

# 3. Windows Disk Cleanup (mo cua so)
Write-Host "`n[3] Mo Windows Disk Cleanup..." -ForegroundColor Yellow
Start-Process cleanmgr -ArgumentList "/d C" -Wait
Write-Host "  Chon 'Clean up system files' de xoa Windows Update cache" -ForegroundColor Gray

Write-Host "`n=== Xong ===" -ForegroundColor Green
Write-Host "Kiem tra lai: Get-PSDrive C | Select Used, Free" -ForegroundColor Gray
