# Palworld — Hướng dẫn Migrate Save & Vận hành Dedicated Server

Tài liệu tổng hợp toàn bộ quy trình: migrate nhân vật host từ listen-server sang dedicated server, backup qua Telegram, provisioning instance mới, và các lỗi thường gặp + cách fix.

---

## 0. Bối cảnh & khái niệm

- **Listen server (co-op / chơi chung máy host):** nhân vật host **luôn** có UID đặc biệt `00000000000000000000000000000001`. Các bạn khác có UID Steam thật.
- **Dedicated server:** mỗi tài khoản Steam có 1 UID **cố định**, suy ra từ SteamID64 (vd `1929C8E5...`, `347D5DE9...`). Mỗi account = 1 file `Players/<UID>.sav` cố định.
- Vấn đề: khi bê save từ listen server lên dedicated, nhân vật host (UID `000...001`) không khớp UID dedicated của chính người đó → host phải chơi lại từ đầu, trong khi các bạn khác vẫn vào bình thường.
- **Giải pháp:** đổi UID host `000...001` → UID dedicated thật của người đó, giữ nguyên đồ đạc / tiến độ / guild.

### Định dạng save (để hiểu tại sao patch kiểu binary)
- File `.sav` = header nén + GVAS. Magic `PlZ` = zlib, `PlM` = Oodle.
- `Level.sav` chứa `CharacterSaveParameterMap` (nhân vật) + `GroupSaveDataMap` (guild).
- UID player serialize kiểu **byte-swapped**: 4 byte đầu = đảo hex prefix, phần còn lại = 0. Host `000...001` = 12 byte `00` + `01 00 00 00`.

---

## 1. ⚠️ Tại sao KHÔNG dùng tool decode→JSON→encode

`palworld_save_tools` bundled trong `D:\Tool\palworld-host-save-fix-main` **cũ hơn** phiên bản game hiện tại.

Chạy `fix_host_save.py` gốc (giải mã cả `Level.sav` ra JSON rồi encode lại) → **làm hỏng `Level.sav`** → server crash `Save data is corrupted` → **tất cả mọi người bị văng, không riêng host**. Disable decoder chỉ tránh crash lúc đọc chứ vẫn hỏng lúc ghi. ⇒ Tool này **không dùng được** cho version save này.

---

## 2. ✅ Giải pháp: Binary Patch (length-preserving)

Thay vì serialize lại toàn bộ GVAS, chỉ **thay đúng các GUID 16-byte** (host → UID mới) ngay trong dữ liệu đã giải nén, giữ nguyên độ dài & cấu trúc, rồi nén lại (PlZ/zlib, giữ nguyên save_type byte). Cấu trúc không đổi → không hỏng.

### Script

| Script | Dùng khi |
|---|---|
| `D:\Tool\fix_uid_migrate.py` | **Khuyên dùng.** Tổng quát, 2 chiều. Truyền `<old_uid> <new_uid>`. |
| `D:\Tool\fix_host_binary_patch.py` | Bản cũ, hardcode source = host `000...001`. |

### Cách chạy

Chạy **từ trong** `D:\Tool\palworld-host-save-fix-main` (cần package + `oo2core_9_win64.dll` để giải nén Oodle).

```powershell
cd D:\Tool\palworld-host-save-fix-main

# Chiều local -> dedicated (host 000...001 -> UID thật)
python D:\Tool\fix_uid_migrate.py <world_dir> 00000000000000000000000000000001 <new_uid32>

# Chiều ngược dedicated -> local (đem char về máy host)
python D:\Tool\fix_uid_migrate.py <world_dir> <old_uid32> 00000000000000000000000000000001
```

- Chạy **không có `--write`** trước = dry-run (chỉ kiểm tra & báo các slot sẽ đổi).
- Thêm `--write <out_dir>` để xuất file đã patch.
- `<world_dir>` phải chứa `Level.sav` + `Players/<old_uid>.sav`.

### Ví dụ thực tế đã chạy

```powershell
# Puddy case
python D:\Tool\fix_uid_migrate.py `
  "D:\Tool\SaveGames\SaveGames\76561198146942237\17C0CB8DB8F243BD9FB698802EE8EC09" `
  00000000000000000000000000000001 347D5DE9000000000000000000000000 `
  --write D:\Tool\PATCHED_OUTPUT

# Zorokun case
python D:\Tool\fix_uid_migrate.py `
  "D:\Tool\...\322EDD6B4B44E21D393BDE815821E885" `
  00000000000000000000000000000001 1929C8E5000000000000000000000000 `
  --write D:\Tool\PATCHED_322EDD6B
```

### Patch cái gì (6 slot GUID — mirror y hệt fix chuẩn)
- **Level.sav:** (1) char-key `PlayerUId`, (2) guild handle guid của instance nhân vật đó, (3) guild `admin_player_uid`, (4) entry của host trong danh sách player của guild.
- **Player .sav:** (5)(6) 2 field `PlayerUId`.
- Định vị bằng **anchor** (host InstanceId + cấu trúc guild) nên đúng bất kể offset.
- **Pal handles để nguyên UID host — cố ý.** Nhờ vậy migrate ngược lại `000...001` sẽ tự đoàn tụ với pals.

### Gotcha đã học
- Version game này: entry player trong guild có **thêm 1 byte trailing** sau tên mỗi player (làm parser cũ hỏng).
- UID đích thường **đã tồn tại sẵn** như 1 nhân vật level thấp + guild solo (tạo ra khi host join dedicated lần đầu). Sau migrate nó bị orphan — vô hại.
- **Luôn xác nhận UID đích đúng là người đó** (check tên char trong guild) trước khi overwrite `<newuid>.sav`.

---

## 3. Deploy save đã patch lên server

```
1. Stop container
2. Backup save hiện tại
3. Upload Level.sav + <newuid>.sav đã patch
4. Xóa file cũ 000...001.sav
5. ⚠️ XÓA WorldOption.sav  (xem mục 4)
6. chown -R 1000:1000 + chmod -R 755
7. Start container
```

Server: `public-node` = `ubuntu@<SERVER_IP>` (key `~/.ssh/<your_key>`, passwordless sudo).
Data: `/home/ubuntu/palworld-data/Pal/Saved/SaveGames/0/<world>/`.

---

## 4. ⚠️ Gotcha lớn: `WorldOption.sav` (co-op → dedicated)

**Triệu chứng:** REST API spam `Unauthorized (AdminPassword is empty)` (HTTP 401) mỗi vài giây, player-logging / REST admin chết — **dù `PalWorldSettings.ini` và env `ADMIN_PASSWORD` đều đúng**.

**Nguyên nhân:** world migrate từ listen server kéo theo `WorldOption.sav` chứa OptionSettings đã "bake" sẵn (với AdminPassword **rỗng**, vì listen server không có admin password). Dedicated server **ưu tiên `WorldOption.sav` đè lên `PalWorldSettings.ini`**.

**Fix:**
```bash
rm <world>/WorldOption.sav
# rồi restart container -> server tự tạo lại từ .ini
```
Không đụng `Level.sav`/tiến độ.

**Verify:**
```bash
docker exec test-runner curl -s -o /dev/null -w "HTTP=%{http_code}\n" \
  -u admin:<ADMIN_PASSWORD> http://127.0.0.1:8212/v1/api/info
# phải trả 200, không phải 401
```

> Phụ: migrate cũng kéo theo client `GameUserSettings.ini` (graphics/DLSS + `DedicatedServerName` bị trùng) — vô hại nhưng lộn xộn; dòng `DedicatedServerName` cuối cùng thắng.

---

## 5. Docker — cấu hình chạy server

Image `thijsvanloef/palworld-server-docker:latest`, container `test-runner`.

```bash
docker run -d --name test-runner --restart unless-stopped \
  -p 8211:8211/udp \
  -v /home/ubuntu/palworld-data:/palworld \
  -e PUID=1000 -e PGID=1000 -e TZ=UTC \
  -e PLAYERS=32 -e SERVER_NAME=test-runner \
  -e SERVER_PASSWORD=<CHANGE_ME> -e ADMIN_PASSWORD=<CHANGE_ME> \
  -e MULTITHREADING=true -e COMMUNITY=false \
  -e RCON_ENABLED=false \
  -e REST_API_ENABLED=true -e REST_API_PORT=8212 \
  -e UPDATE_ON_BOOT=true \
  thijsvanloef/palworld-server-docker:latest
```

One-shot provisioning script: **`D:\Tool\provision_palworld.sh`** — cài Docker (get.docker.com) + zip/curl/unzip/cron, optional restore từ `/home/ubuntu/restore/palworld_save.zip`, pull + run với config trên, và nhúng luôn script backup Telegram.

---

## 6. Backup tự động qua Telegram (cron)

Script trên server: `/home/ubuntu/backup_palworld.sh` (chmod 700).
- Zip `/home/ubuntu/palworld-data/Pal/Saved` (relative path, loại `*.log`/`*.tmp`).
- Gửi Telegram `sendDocument` (giới hạn 50MB, guard 49MB).
- Chỉ xóa zip local khi response `"ok":true`. Log ra `/home/ubuntu/backups/backup.log`.
- `CHAT_ID="<your_chat_id>"` (chat riêng).

Cron (crontab của `ubuntu`):
```cron
0 20 * * * /home/ubuntu/backup_palworld.sh
```
= 20:00 UTC = **03:00 giờ VN** mỗi ngày.

---

## 7. ⚠️ Bài học quan trọng: quoting PowerShell ↔ ssh ↔ bash

Ngoặc `()`, Go template `{{}}`, và nested quotes **bị mangle** khi truyền qua nhiều lớp shell.

**Cách chạy đáng tin cậy:**
```
1. Viết script ra file local
2. scp lên server
3. ssh chạy:  sed -i 's/\r$//' file   (bỏ CRLF của Windows)
4. rồi mới chạy / crontab file
```
Cài cron cũng vậy: ghi nội dung cron ra file local → scp → `crontab /file` (đừng pipe string qua ssh).

---

## Tóm tắt checklist migrate (co-op → dedicated)

- [ ] `cd D:\Tool\palworld-host-save-fix-main`
- [ ] Dry-run `fix_uid_migrate.py <world> 000...001 <new_uid>`
- [ ] Xác nhận UID đích đúng người (tên char trong guild)
- [ ] Chạy lại với `--write <out>`
- [ ] Stop container → backup
- [ ] Upload `Level.sav` + `<new_uid>.sav`, xóa `000...001.sav`
- [ ] **Xóa `WorldOption.sav`**
- [ ] `chown -R 1000:1000` + `chmod -R 755`
- [ ] Start → verify REST 200
