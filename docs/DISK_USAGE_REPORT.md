# Báo cáo dung lượng ổ đĩa C:

**Tổng quan:** 237 GB | Đã dùng: 236 GB | **Còn trống: ~1 GB** (rất nguy hiểm!)

---

## Các thư mục chiếm nhiều dung lượng nhất

| Thư mục | Dung lượng | Ghi chú |
|---------|------------|---------|
| **Riot Games** | 34 GB | Game (Valorant, League of Legends...) |
| **Windows** | 31.5 GB | Hệ điều hành |
| **ProgramData\Microsoft\Windows\Virtual Hard Disks** | 31 GB | ub-24.vhdx (19 GB), extend.vhdx (12 GB) |
| **Docker (AppData\Local\Docker\wsl)** | **21.4 GB** | docker_data.vhdx - data Docker/WSL |
| **Program Files** | 15.5 GB | Ứng dụng |
| **Users** | 14.6 GB | Downloads (4.7 GB), Documents (4.7 GB) |
| **Program Files (x86)** | 9 GB | Ứng dụng 32-bit |
| **isowin11** | 5.2 GB | File ISO Windows 11 |
| **AppData\Local\Temp** | 3.5 GB | File tạm |
| **.cursor** | 1.1 GB | Cache Cursor IDE |
| **.nuget** | 1.2 GB | Cache NuGet |

---

## Khuyến nghị giải phóng dung lượng

### 1. Docker (~21 GB) - Ưu tiên cao
Bạn đã chuyển Docker sang Hyper-V. Có thể dọn Docker:
```powershell
# Đóng Docker Desktop trước
docker system prune -a --volumes
# Hoặc: Docker Desktop > Settings > Resources > Disk image size > Clean / Purge
```

### 2. Xóa file tạm (~3.5 GB)
```powershell
Remove-Item "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue
```

### 3. isowin11 (~5 GB)
Xóa nếu không cần cài Windows nữa.

### 4. Riot Games (~34 GB)
Gỡ game nếu không chơi.

### 5. Virtual Hard Disks - ub-24.vhdx (~19 GB)
Nếu không dùng WSL/Ubuntu:
```powershell
wsl --unregister Ubuntu-24.04  # hoặc tên distro tương ứng
```

### 6. Windows Disk Cleanup
Chạy `cleanmgr` → chọn ổ C → Clean up system files.
