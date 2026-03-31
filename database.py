"""
database.py — SQLite helper.
Handles schema creation dan semua operasi DB.
Games tidak lagi di-cache — queue pakai filepath langsung.
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

def now_wib() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS switches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address  TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL DEFAULT '',
                ftp_port    INTEGER NOT NULL DEFAULT 5000,
                added_at    TEXT NOT NULL DEFAULT (datetime('now', '+7 hours'))
            );

            CREATE TABLE IF NOT EXISTS queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                switch_id   INTEGER NOT NULL REFERENCES switches(id),
                filepath    TEXT NOT NULL,
                filename    TEXT NOT NULL,
                filesize    INTEGER NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                progress    INTEGER NOT NULL DEFAULT 0,
                speed_kbps  INTEGER NOT NULL DEFAULT 0,
                error_msg   TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+7 hours')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now', '+7 hours'))
            );

            CREATE TABLE IF NOT EXISTS transfer_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id    INTEGER NOT NULL REFERENCES queue(id),
                event       TEXT NOT NULL,
                detail      TEXT,
                logged_at   TEXT NOT NULL DEFAULT (datetime('now', '+7 hours'))
            );
        """)


def reset_stuck_transfers():
    with get_conn() as conn:
        conn.execute("""
            UPDATE queue SET status='pending', progress=0, updated_at=?
            WHERE status='transferring'
        """, (now_wib(),))


def get_switches_with_pending() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT s.*
            FROM switches s
            JOIN queue q ON q.switch_id = s.id
            WHERE q.status IN ('pending', 'error')
            AND q.retry_count < 3
        """).fetchall()
        return [dict(r) for r in rows]


# ─── Settings ────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )


# ─── Switches ────────────────────────────────────────────────────────────────

def get_all_switches() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM switches ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_switch_by_ip(ip: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM switches WHERE ip_address=?", (ip,)).fetchone()
        return dict(row) if row else None


def add_switch(ip: str, name: str = "", ftp_port: int = 5000) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO switches(ip_address, name, ftp_port) VALUES(?,?,?)",
            (ip, name, ftp_port)
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM switches WHERE ip_address=?", (ip,)).fetchone()
        return row["id"]


def update_switch_name(switch_id: int, name: str):
    with get_conn() as conn:
        conn.execute("UPDATE switches SET name=? WHERE id=?", (name, switch_id))


def delete_switch(switch_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM switches WHERE id=?", (switch_id,))


# ─── Queue ───────────────────────────────────────────────────────────────────

def enqueue(switch_id: int, filepath: str, filename: str, filesize: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO queue(switch_id, filepath, filename, filesize) VALUES(?,?,?,?)",
            (switch_id, filepath, filename, filesize)
        )
        return cur.lastrowid


def get_queue_for_switch(switch_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM queue
            WHERE switch_id=? AND status IN ('pending','transferring','error','paused')
            ORDER BY id ASC
        """, (switch_id,)).fetchall()
        return [dict(r) for r in rows]


def get_all_queue_status() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT q.*, s.name AS switch_name, s.ip_address
            FROM queue q
            JOIN switches s ON s.id = q.switch_id
            ORDER BY q.switch_id, q.id
        """).fetchall()
        return [dict(r) for r in rows]


def update_queue_status(queue_id: int, status: str, progress: int = None,
                        speed_kbps: int = None, error_msg: str = None):
    with get_conn() as conn:
        fields = ["status=?", "updated_at=?"]
        values = [status, now_wib()]
        if progress is not None:
            fields.append("progress=?"); values.append(progress)
        if speed_kbps is not None:
            fields.append("speed_kbps=?"); values.append(speed_kbps)
        if error_msg is not None:
            fields.append("error_msg=?"); values.append(error_msg)
        values.append(queue_id)
        conn.execute(f"UPDATE queue SET {', '.join(fields)} WHERE id=?", values)


def increment_retry(queue_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue SET retry_count=retry_count+1, updated_at=? WHERE id=?",
            (now_wib(), queue_id)
        )


# ─── Transfer Log ─────────────────────────────────────────────────────────────

def log_event(queue_id: int, event: str, detail: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO transfer_log(queue_id, event, detail) VALUES(?,?,?)",
            (queue_id, event, detail)
        )


def get_recent_logs(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT tl.*, s.name AS switch_name, q.filename
            FROM transfer_log tl
            JOIN queue q ON q.id = tl.queue_id
            JOIN switches s ON s.id = q.switch_id
            ORDER BY tl.id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ─── USB Switches ─────────────────────────────────────────────────────────────

def init_usb_switches_table():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usb_switches (
                serial      TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                added_at    TEXT NOT NULL DEFAULT (datetime('now', '+7 hours'))
            );

            CREATE TABLE IF NOT EXISTS usb_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                serial      TEXT NOT NULL REFERENCES usb_switches(serial),
                filepath    TEXT NOT NULL,
                filename    TEXT NOT NULL,
                filesize    INTEGER NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+7 hours'))
            );
        """)


def get_all_usb_switches() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM usb_switches ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def upsert_usb_switch(serial: str, name: str = "") -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO usb_switches(serial, name) VALUES(?, ?)
            ON CONFLICT(serial) DO UPDATE SET name=CASE WHEN excluded.name!='' THEN excluded.name ELSE name END
        """, (serial, name))


def update_usb_switch_name(serial: str, name: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE usb_switches SET name=? WHERE serial=?", (name, serial))


def delete_usb_switch(serial: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM usb_queue WHERE serial=?", (serial,))
        conn.execute("DELETE FROM usb_switches WHERE serial=?", (serial,))


def get_usb_queue(serial: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM usb_queue WHERE serial=? ORDER BY id",
            (serial,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_usb_queue(serial: str, files: list[dict]) -> None:
    """Replace queue untuk serial ini dengan list file baru."""
    with get_conn() as conn:
        conn.execute("DELETE FROM usb_queue WHERE serial=?", (serial,))
        for f in files:
            conn.execute(
                "INSERT INTO usb_queue(serial, filepath, filename, filesize) VALUES(?,?,?,?)",
                (serial, f['filepath'], f['filename'], f.get('filesize', 0))
            )


def mark_usb_game_done(serial: str, filename: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE usb_queue SET status='done' WHERE serial=? AND filename=? AND status='pending'",
            (serial, filename)
        )


def clear_usb_queue_done(serial: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM usb_queue WHERE serial=? AND status='done'", (serial,))
