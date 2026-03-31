"""
queue_manager.py — Logika queue, worker per Switch, retry logic.

Setiap Switch punya satu worker thread yang memproses game satu per satu.
Antar Switch berjalan paralel (thread terpisah).
"""

import threading
import time
import database as db
import ftp_engine as ftp
from config import MAX_RETRY, RETRY_DELAY


# Worker threads aktif: {switch_id: Thread}
_workers: dict[int, threading.Thread] = {}
_workers_lock = threading.Lock()

# Kontrol per Switch: "run" | "pause" | "cancel"
# Dicek di setiap progress callback — kalau bukan "run", transfer dihentikan
_control: dict[int, str] = {}
_control_lock = threading.Lock()


class TransferInterrupted(Exception):
    """Raised saat user minta pause atau cancel."""
    pass


# ─── Public API ──────────────────────────────────────────────────────────────

def enqueue_games(switch_id: int, game_ids: list[int]):
    """Tambahkan game ke queue Switch, lalu pastikan worker aktif."""
    for game_id in game_ids:
        db.enqueue(switch_id, game_id)
    _set_control(switch_id, "run")
    _ensure_worker(switch_id)


def pause_switch(switch_id: int):
    """Pause transfer yang sedang berjalan di Switch ini."""
    _set_control(switch_id, "pause")


def resume_switch(switch_id: int):
    """Resume transfer yang di-pause. Item paused kembali ke pending."""
    _set_control(switch_id, "run")
    # Reset item paused ke pending supaya worker pick up lagi
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE queue SET status='pending' WHERE switch_id=? AND status='paused'",
            (switch_id,)
        )
    _ensure_worker(switch_id)


def cancel_all(switch_id: int):
    """Cancel semua item pending/error di queue Switch ini."""
    _set_control(switch_id, "cancel_item")  # interrupt kalau sedang transfer
    with db.get_conn() as conn:
        # Ambil semua item yang bisa di-cancel
        rows = conn.execute(
            "SELECT id FROM queue WHERE switch_id=? AND status IN ('pending','error','paused','transferring')",
            (switch_id,)
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE queue SET status='cancelled' WHERE id=?", (row["id"],))
            db.log_event(row["id"], "error", "Dibatalkan oleh operator (cancel all)")
    # Reset kontrol setelah cancel
    _set_control(switch_id, "run")


def cancel_item(queue_id: int):
    """Cancel satu item queue — hapus dari proses, tandai cancelled."""
    with db.get_conn() as conn:
        # Ambil switch_id dan status sekaligus — satu query, tidak ada race condition
        row = conn.execute(
            "SELECT switch_id, status FROM queue WHERE id=?", (queue_id,)
        ).fetchone()
        if row:
            sw_id  = row["switch_id"]
            status = row["status"]
            if status == "transferring":
                # Sedang transfer — sinyal interrupt, worker akan tangkap di progress callback
                _set_control(sw_id, "cancel_item")
            # Status error/pending/paused — langsung cancel tanpa perlu interrupt
        conn.execute("UPDATE queue SET status='cancelled' WHERE id=?", (queue_id,))
    db.log_event(queue_id, "error", "Dibatalkan oleh operator")


def get_status() -> list[dict]:
    """Snapshot seluruh queue + info Switch untuk dashboard."""
    return db.get_all_queue_status()


def get_switch_control(switch_id: int) -> str:
    with _control_lock:
        return _control.get(switch_id, "run")


# ─── Internal ─────────────────────────────────────────────────────────────────

def _set_control(switch_id: int, action: str):
    with _control_lock:
        _control[switch_id] = action


def _ensure_worker(switch_id: int):
    """Jalankan worker thread kalau belum ada / sudah mati."""
    with _workers_lock:
        t = _workers.get(switch_id)
        if t and t.is_alive():
            return
        t = threading.Thread(
            target=_worker_loop,
            args=(switch_id,),
            daemon=True,
            name=f"worker-sw{switch_id}"
        )
        _workers[switch_id] = t
        t.start()


def _worker_loop(switch_id: int):
    """Loop utama — ambil item pending, kirim, ulangi sampai queue kosong."""
    while True:
        # Cek pause — tunggu sampai di-resume
        while get_switch_control(switch_id) == "pause":
            time.sleep(1)

        items = db.get_queue_for_switch(switch_id)
        item = next(
            (i for i in items if i["status"] in ("pending", "error")
             and i["retry_count"] < MAX_RETRY),
            None
        )
        if not item:
            break  # queue kosong — thread selesai

        switches = db.get_all_switches()
        sw = next((s for s in switches if s["id"] == switch_id), None)
        if not sw:
            break

        _transfer(switch_id, item["id"], sw, item)


def _transfer(switch_id: int, queue_id: int, sw: dict, item: dict):
    """Kirim satu file, handle pause/cancel/retry."""
    db.update_queue_status(queue_id, "transferring", progress=0)
    db.log_event(queue_id, "connect", f"Connecting to {sw['ip_address']}")

    def on_progress(sent, total, speed_kbps):
        # Cek kontrol setiap chunk — ini cara interrupt transfer yang sedang jalan
        ctrl = get_switch_control(switch_id)
        if ctrl in ("pause", "cancel_item"):
            raise TransferInterrupted(ctrl)
        pct = int(sent / total * 100) if total else 0
        db.update_queue_status(queue_id, "transferring",
                               progress=pct, speed_kbps=speed_kbps)

    try:
        ftp.push_file(
            ip=sw["ip_address"],
            port=sw["ftp_port"],
            local_path=item["filepath"],
            progress_callback=on_progress,
        )
        db.update_queue_status(queue_id, "done", progress=100, speed_kbps=0)
        db.log_event(queue_id, "done", item["filename"])

    except TransferInterrupted as e:
        action = str(e)
        if action == "pause":
            db.update_queue_status(queue_id, "paused",
                                   error_msg="Di-pause oleh operator")
            db.log_event(queue_id, "error", "Transfer di-pause")
        else:  # cancel_item
            db.update_queue_status(queue_id, "cancelled",
                                   error_msg="Dibatalkan oleh operator")
            db.log_event(queue_id, "error", "Transfer dibatalkan")
            _set_control(switch_id, "run")  # reset kontrol setelah cancel item

    except ftp.FTPError as e:
        db.increment_retry(queue_id)
        retry_count = item["retry_count"] + 1
        db.log_event(queue_id, "error", str(e))

        if retry_count >= MAX_RETRY:
            db.update_queue_status(queue_id, "error",
                                   error_msg=f"Gagal setelah {MAX_RETRY}x retry: {e}")
            db.log_event(queue_id, "retry", f"Max retry ({MAX_RETRY}x) tercapai")
        else:
            db.update_queue_status(queue_id, "error",
                                   error_msg=f"Retry {retry_count}/{MAX_RETRY}: {e}")
            db.log_event(queue_id, "retry",
                         f"Coba lagi {retry_count}/{MAX_RETRY} dalam {RETRY_DELAY}s")
            time.sleep(RETRY_DELAY)
            db.update_queue_status(queue_id, "pending")
