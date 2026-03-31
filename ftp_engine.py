"""
ftp_engine.py — FTP client untuk push file ke Switch via Sphaira.
Modul ini terpisah agar mudah diganti protokol lain kalau perlu.

Cara kerja Sphaira FTP (terkonfirmasi dari test):
- Login: anonymous, tanpa password
- Mode: PASV (passive) — wajib
- Upload: CWD ke "/" lalu STOR filename saja (tanpa path prefix)
- Sphaira auto-detect dan install file dari root secara otomatis
"""

import ftplib
import os
import time
from config import SWITCH_FTP_PORT, SWITCH_FTP_USER, SWITCH_FTP_PASS, \
                   FTP_TIMEOUT, TRANSFER_CHUNK_SIZE


class FTPError(Exception):
    pass


def _connect(ip: str, port: int) -> ftplib.FTP:
    """Buat koneksi FTP ke Sphaira. Passive mode wajib."""
    ftp = ftplib.FTP()
    ftp.connect(ip, port, timeout=FTP_TIMEOUT)
    ftp.set_pasv(True)
    try:
        ftp.login(SWITCH_FTP_USER or "anonymous", SWITCH_FTP_PASS or "")
    except ftplib.error_perm:
        pass  # Sphaira kadang tidak butuh login
    return ftp


def test_connection(ip: str, port: int = None) -> tuple[bool, str]:
    """
    Test koneksi FTP ke Switch.
    Return (True, "") kalau berhasil, (False, pesan_error) kalau gagal.
    """
    port = port or SWITCH_FTP_PORT
    try:
        ftp = _connect(ip, port)
        ftp.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


def push_file(ip: str, port: int, local_path: str,
              progress_callback=None) -> bool:
    """
    Upload satu file ke Switch via FTP.

    Sphaira menerima file yang di-upload ke root "/" dengan nama file saja.
    Setelah file diterima, Sphaira otomatis memproses dan menginstall game.

    progress_callback(bytes_sent: int, total_bytes: int, speed_kbps: int)
        dipanggil tiap chunk untuk update progress di dashboard.

    Return True kalau sukses, raise FTPError kalau gagal.
    """
    filename    = os.path.basename(local_path)
    total_bytes = os.path.getsize(local_path)

    try:
        ftp = _connect(ip, port)
        ftp.cwd("/")  # Sphaira: upload ke root, auto-install dari sana

        bytes_sent = 0
        start_time = time.time()

        with open(local_path, "rb") as f:
            def handle_chunk(data):
                nonlocal bytes_sent
                bytes_sent += len(data)
                elapsed    = max(time.time() - start_time, 0.001)
                speed_kbps = int((bytes_sent / elapsed) / 1024)
                if progress_callback:
                    progress_callback(bytes_sent, total_bytes, speed_kbps)

            ftp.storbinary(f"STOR {filename}", f,
                           blocksize=TRANSFER_CHUNK_SIZE,
                           callback=handle_chunk)

        ftp.quit()
        return True

    except ftplib.all_errors as e:
        raise FTPError(str(e)) from e
