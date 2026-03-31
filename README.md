# Switch Auto Installer

Homebrew Nintendo Switch untuk auto-install game dari NAS via USB — tanpa perlu klik manual di setiap game.

## Cara Kerja

1. Queue game sudah disiapkan dari dashboard web NAS
2. Buka homebrew ini di Switch
3. Homebrew otomatis konek ke NAS via USB
4. Semua game di queue diinstall otomatis
5. Kalau ada yang gagal → retry otomatis (max 3x)
6. Selesai semua → tampilkan summary

## Setup

### Di Switch
Copy ke SD card:
```
sdmc:/switch/switch-installer/switch-installer.nro
sdmc:/switch/switch-installer/queue.txt
```

File `queue.txt` berisi daftar nama file game, satu per baris:
```
Mario Kart 8 Deluxe.nsp
Pokemon Scarlet.nsp
Zelda BOTW.xci
```

> **Note:** File queue.txt otomatis di-generate dari dashboard NAS — tidak perlu edit manual.

### Di NAS
Tidak ada perubahan — `dbibackend.py` dan `usb_monitor.sh` tetap sama.

## Build

Build otomatis via GitHub Actions setiap push ke branch `main`.
Download hasil build dari tab **Actions** → pilih run terbaru → **Artifacts**.

### Build manual
```bash
# Install devkitPro dulu
# https://devkitpro.org/wiki/Getting_Started

make
```

## Kontrol di Switch

| Tombol | Fungsi |
|--------|--------|
| `+`    | Keluar |

Tidak ada kontrol lain — homebrew ini fully automatic.

## Kompatibilitas Protokol

Menggunakan protokol DBI backend yang sama persis — compatible dengan `dbibackend.py` yang sudah jalan di NAS tanpa perlu modifikasi apapun.
