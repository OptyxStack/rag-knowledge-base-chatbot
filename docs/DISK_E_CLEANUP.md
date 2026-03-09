# Dọn ổ E - Đã thực hiện

## Đã xóa
- **Downloads** (~1.8 GB)
- **DeliveryOptimization** (Windows cache)
- **WUDownloadCache** (Windows Update cache)
- **app\_broken_migrations** (thư mục rác)

## Hiện trạng ổ E
- **Đã dùng:** ~23 GB
- **Còn trống:** ~214 GB

## Các thư mục chiếm dung lượng
| Thư mục | Dung lượng | Ghi chú |
|---------|------------|---------|
| Docker | 11.8 GB | Docker VM (images, containers) |
| docs | 6 GB | Project docs |
| Program Files | 1 GB | Ứng dụng |

## Để giải phóng thêm dung lượng

### 1. Chuyển Docker về ổ C (~12 GB)
Nếu ổ C có chỗ trống:
- Docker Desktop > Settings > Resources > Advanced > Disk image location
- Đổi từ `E:\Docker` sang `C:\Users\Administrator\AppData\Local\Docker`
- Apply & Restart
- Xóa thư mục `E:\Docker` sau khi xong

### 2. Docker prune (khi Docker chạy ổn định)
```powershell
docker system prune -a --volumes
```

### 3. Compact Docker VHDX (cần Admin)
```powershell
Optimize-VHD -Path "E:\Docker\DockerDesktop\DockerDesktop.vhdx" -Mode Full
```
