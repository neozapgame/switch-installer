"""
config.py — Konfigurasi global aplikasi.
Nilai default bisa dioverride via UI Settings dan disimpan ke SQLite.
"""

# Server
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 8080
DEBUG = False

# FTP ke Switch (Sphaira)
# Upload ke root "/" dengan filename saja — Sphaira auto-install dari sana
SWITCH_FTP_PORT = 5000          # port default Sphaira
SWITCH_FTP_USER = ""            # anonymous login, tidak perlu password
SWITCH_FTP_PASS = ""

# Transfer
FTP_TIMEOUT = 30                # detik, timeout per koneksi FTP
TRANSFER_CHUNK_SIZE = 1024 * 1024  # 1MB per chunk — lebih stabil untuk WiFi

# Retry logic
MAX_RETRY = 3
RETRY_DELAY = 30                # detik sebelum retry

# Library scan
SUPPORTED_EXTENSIONS = (".nsp", ".xci", ".nsz")

# Polling interval dashboard (ms)
POLL_INTERVAL_MS = 2500
