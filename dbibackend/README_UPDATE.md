# Switch Install Server — Update Notes

## Perubahan di sesi ini

### 1. usb_monitor.sh → Systemd Service (single instance)

**Masalah lama:** Bisa jalan multiple instance kalau di-run manual.

**Solusi:**
- Lockfile di `/var/run/usb_monitor.lock` — instance kedua langsung exit
- Trap signal `INT`/`TERM` → graceful shutdown (stop semua container dbi-*)
- Health check tiap 2s: kalau container crash, otomatis restart
- File service: `usb-monitor.service`

**Install:**
```bash
# Di host NAS (via SSH)
sh install_service.sh /volume1/Switch http://NAS_IP:8080

# Manual management:
systemctl status  usb-monitor
systemctl restart usb-monitor
journalctl -u usb-monitor -f   # live log
```

---

### 2. Real-time Status Update (dbibackend → server → dashboard)

**Masalah lama:** Dashboard hanya polling DB, tidak tahu kalau game selesai di container.

**Solusi:**
- `dbibackend.py` sekarang POST ke `SERVER_URL/api/usb/switches/<serial>/notify`
  setiap game selesai transfer
- `server.py` punya endpoint baru `/api/usb/switches/<serial>/notify`
  yang update DB + sync queue file
- Dashboard polling **adaptive**: 1.5s saat ada transfer aktif, 4s saat idle
- Queue list di dashboard tampilkan status: ✓ done | ⏳ pending | ✗ error

**Env var yang diperlukan di container dbibackend:**
```
SERVER_URL=http://NAS_IP:8080
```
(sudah di-set di `usb_monitor.sh` via env `SERVER_URL`)

---

### 3. Resume saat Reconnect

**Masalah lama:** Kalau Switch dicabut saat transfer, reconnect mulai dari awal.

**Solusi:**
- Queue file `/tmp/queue_SERIAL.json` adalah source of truth
- `dbibackend.py` saat `wait_for_queue()` hanya ambil item `status='pending'`
- Item yang sudah `done` di-skip otomatis
- Saat reconnect, `usb_monitor.sh` restart container → container baca queue file
  yang sama → lanjut dari game yang belum done

**Flow resume:**
```
Switch dicabut → container stop
Switch colok lagi → container baru start dengan serial sama
Container baca /tmp/queue_SERIAL.json → hanya kirim yang status='pending'
Game yang sudah done tidak dikirim ulang ✓
```

---

### 4. Multi-Switch (5 sekaligus)

**Sudah di-support sebelumnya**, tapi ada improvement:
- `usb_monitor.sh` sekarang health-check tiap container tiap 2s
- Kalau salah satu container crash (OOM, USB error), auto-restart
- Logging per-container jelas dengan nama `dbi-<serial>`

**Monitor 5 Switch sekaligus:**
```bash
# Log semua container dbi-*
docker ps --filter 'name=dbi-'
docker logs -f dbi-SERIAL1 &
docker logs -f dbi-SERIAL2 &

# Atau lewat journalctl
journalctl -u usb-monitor -f
```

---

### File yang berubah

| File | Perubahan |
|------|-----------|
| `dbibackend/usb_monitor.sh` | Lockfile, trap signal, health check, SERVER_URL env |
| `dbibackend/usb-monitor.service` | **Baru** — systemd service file |
| `dbibackend/install_service.sh` | **Baru** — script install otomatis |
| `dbibackend/dbibackend.py` | Resume logic, HTTP notify ke server |
| `switchserver/server.py` | Endpoint `/notify` baru |
| `switchserver/static/app.js` | Adaptive polling, status icon queue |

