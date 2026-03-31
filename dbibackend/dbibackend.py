#!/usr/bin/python3
"""
dbibackend — Single Switch USB installer.
1 container = 1 Switch. Queue by serial number.

Fitur:
- Kirim HTTP callback ke server saat game selesai (real-time status update)
- Resume: saat reconnect, hanya kirim game yang belum 'done' di queue file
- Queue file format: [{filepath, filename, filesize, status}]
"""

import usb.core
import usb.util
import struct
import sys
import time
import logging
import os
import json
import threading
import urllib.request
import urllib.error
from enum import IntEnum
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
log.addHandler(handler)
log.setLevel(logging.INFO)

BUFFER_SEGMENT_DATA_SIZE = 0x100000
SWITCH_VID    = 0x057E
SWITCH_PID    = 0x3000
WORK_DIR      = os.environ.get('GAMES_DIR', '/games')
SWITCH_BUS    = os.environ.get('SWITCH_BUS', '')
SWITCH_DEV    = os.environ.get('SWITCH_DEV', '')
SWITCH_SERIAL = os.environ.get('SWITCH_SERIAL', '')
QUEUE_DIR     = os.environ.get('QUEUE_DIR', '/tmp')
SERVER_URL    = os.environ.get('SERVER_URL', 'http://host.docker.internal:8080')


class CommandID(IntEnum):
    EXIT            = 0
    LIST_DEPRECATED = 1
    FILE_RANGE      = 2
    LIST            = 3

class CommandType(IntEnum):
    REQUEST  = 0
    RESPONSE = 1
    ACK      = 2


# ─── Server Callback ─────────────────────────────────────────────────────────

def notify_server(serial: str, filename: str, status: str, progress: int = None):
    """
    Kirim update ke switchserver agar dashboard real-time.
    POST /api/usb/switches/<serial>/notify
    Body: {"filename": "...", "status": "done"|"transferring", "progress": 0-100}
    """
    if not SERVER_URL or not serial:
        return
    url = f"{SERVER_URL.rstrip('/')}/api/usb/switches/{serial}/notify"
    payload = {"filename": filename, "status": status}
    if progress is not None:
        payload["progress"] = progress
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            log.info(f"Notified server: {filename} → {status} progress={progress} (HTTP {resp.status})")
    except urllib.error.URLError as e:
        log.warning(f"Server notify gagal (non-fatal): {e}")
    except Exception as e:
        log.warning(f"Server notify error (non-fatal): {e}")


# ─── USB Context ─────────────────────────────────────────────────────────────

class UsbContext:
    def __init__(self, dev):
        self.dev           = dev
        self._claimed_intf = None

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:
            pass

        try:
            dev.set_configuration()
        except Exception:
            pass

        cfg      = dev.get_active_configuration()
        intf     = cfg[(0, 0)]
        intf_num = intf.bInterfaceNumber

        try:
            usb.util.claim_interface(dev, intf_num)
            self._claimed_intf = intf_num
        except Exception as e:
            raise ConnectionError(f'claim_interface failed: {e}')

        self._out = usb.util.find_descriptor(
            intf, custom_match=lambda ep: usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        self._in = usb.util.find_descriptor(
            intf, custom_match=lambda ep: usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        if self._out is None or self._in is None:
            raise LookupError('Endpoints not found')

    def read(self, size, timeout=1000):
        return self._in.read(size, timeout=timeout)

    def write(self, data, timeout=1000):
        self._out.write(data, timeout=timeout)

    def close(self):
        if self.dev is None:
            return
        try:
            if self._claimed_intf is not None:
                usb.util.release_interface(self.dev, self._claimed_intf)
                try:
                    self.dev.attach_kernel_driver(self._claimed_intf)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = None


# ─── Queue Management ────────────────────────────────────────────────────────

def get_queue_file():
    if SWITCH_SERIAL:
        return Path(QUEUE_DIR) / f'queue_{SWITCH_SERIAL}.json'
    return None


def wait_for_queue():
    """
    Tunggu queue file muncul.
    Return hanya item dengan status='pending' (resume: skip yang sudah 'done').
    """
    qfile = get_queue_file()
    if qfile is None:
        log.info('No serial set, using full game list')
        return None

    log.info(f'Waiting for queue file: {qfile}')
    while True:
        if qfile.exists():
            try:
                items = json.loads(qfile.read_text())
                pending = [i for i in items if i.get('status') == 'pending']
                done_count = sum(1 for i in items if i.get('status') == 'done')
                if pending:
                    log.info(f'Queue loaded: {len(pending)} pending, {done_count} sudah done (resume from checkpoint)')
                    return pending
                elif items:
                    log.info(f'Semua {len(items)} game sudah done, tidak ada yang perlu dikirim.')
                    # Tunggu sebentar, mungkin user akan tambah game baru
                    time.sleep(3)
                else:
                    log.info('Queue kosong, menunggu...')
            except Exception as e:
                log.warning(f'Queue file error: {e}')
        time.sleep(1)


def mark_done(filename: str):
    """Tandai game sebagai done di queue file + notify server."""
    qfile = get_queue_file()
    if qfile is None or not qfile.exists():
        return
    try:
        items = json.loads(qfile.read_text())
        for item in items:
            if item.get('filename') == filename and item.get('status') == 'pending':
                item['status'] = 'done'
                log.info(f'Marked done: {filename}')
        qfile.write_text(json.dumps(items, ensure_ascii=False))
    except Exception as e:
        log.warning(f'mark_done error: {e}')

    # Callback ke server (async, non-blocking)
    t = threading.Thread(
        target=notify_server,
        args=(SWITCH_SERIAL, filename, 'done'),
        daemon=True
    )
    t.start()


# ─── DBI Protocol ────────────────────────────────────────────────────────────

def send_list(ctx):
    queue = wait_for_queue()
    titles = OrderedDict()

    if queue is not None:
        for item in queue:
            fname = item.get('filename', '')
            fpath = item.get('filepath', '')
            if fname and fpath and Path(fpath).exists():
                titles[fname] = fpath
            elif fname and fpath:
                log.warning(f'File tidak ditemukan, skip: {fpath}')
        log.info(f'Queue mode: {len(titles)} titles akan dikirim ke Switch')
    else:
        for dirName, _, files in os.walk(WORK_DIR):
            for f in sorted(files):
                if f.lower().endswith(('.nsp', '.nsz', '.xci', '.xcz')):
                    titles[f] = str(Path(dirName) / f)
        log.info(f'Full mode: {len(titles)} titles')

    data = '\n'.join(titles.keys()).encode('utf-8')
    ctx.write(struct.pack('<4sIII', b'DBI0', CommandType.RESPONSE, CommandID.LIST, len(data)))
    ctx.read(16)
    ctx.write(data)
    return titles


# Track progress per file: {fname: {"pct": last_pct, "time": last_notify_time}}
_progress_track: dict = {}
# Track files that have been fully transferred (end_offset >= fsize)
_completed_files: set = set()

def send_file_range(ctx, data_size, titles):
    ctx.write(struct.pack('<4sIII', b'DBI0', CommandType.ACK, CommandID.FILE_RANGE, data_size))
    header     = bytes(ctx.read(data_size))
    range_size = struct.unpack('<I', header[:4])[0]
    range_off  = struct.unpack('<Q', header[4:12])[0]
    fname      = bytes(header[16:]).decode('utf-8')
    fpath      = titles.get(fname, fname)

    log.info(f'Sending: {fname} offset={range_off} size={range_size}')
    ctx.write(struct.pack('<4sIII', b'DBI0', CommandType.RESPONSE, CommandID.FILE_RANGE, range_size))
    ctx.read(16)

    # Hitung progress untuk threshold 5% atau 30 detik
    fsize = Path(fpath).stat().st_size if Path(fpath).exists() else range_size
    # Pakai tracker global per file supaya tidak reset tiap chunk
    if fname not in _progress_track:
        _progress_track[fname] = {"pct": -1, "time": 0}
    track = _progress_track[fname]
    # Reset tracker when bulk transfer starts (progress goes backwards)
    if fsize > 0 and range_size >= BUFFER_SEGMENT_DATA_SIZE:
        current_pct = int(range_off / fsize * 100)
        if current_pct < track["pct"]:
            track["pct"] = -1
            track["time"] = 0
            track.pop("last_pct_raw", None)

    with open(fpath, 'rb') as f:
        f.seek(range_off)
        sent  = 0
        chunk = BUFFER_SEGMENT_DATA_SIZE
        while sent < range_size:
            if sent + chunk > range_size:
                chunk = range_size - sent
            ctx.write(f.read(chunk), timeout=5000)
            sent += chunk

            # Hitung progress global (offset + sent) terhadap ukuran file
            if fsize > 0:
                global_sent = range_off + sent
                pct = int(global_sent / fsize * 100)
                # Notify tiap 5% atau tiap 30 detik
                now = time.time()
                threshold = (pct // 5) * 5
                pct_trigger = threshold > track["pct"]
                time_trigger = (now - track["time"]) >= 30 and pct != track.get("last_pct_raw", -1)
                if (pct_trigger or time_trigger) and pct < 100:
                    track["pct"] = threshold
                    track["time"] = now
                    track["last_pct_raw"] = pct
                    t = threading.Thread(
                        target=notify_server,
                        args=(SWITCH_SERIAL, fname, 'transferring', pct),
                        daemon=True
                    )
                    t.start()

    # Game dianggap selesai kalau offset terakhir mencapai akhir file
    end_offset = range_off + range_size
    if end_offset >= fsize:
        log.info(f'Transfer selesai: {fname}')
        _progress_track.pop(fname, None)
        _completed_files.add(fname)
        mark_done(fname)


# ─── Main Handler ─────────────────────────────────────────────────────────────

def handle_switch(dev):
    name = f'Switch(bus={dev.bus},dev={dev.address},serial={SWITCH_SERIAL or "?"})'
    log.info(f'{name}: Connecting...')
    ctx = None
    try:
        ctx    = UsbContext(dev)
        log.info(f'{name}: Ready')
        titles = None

        while True:
            try:
                hdr = bytes(ctx.read(16, timeout=1000))
            except usb.core.USBTimeoutError:
                continue
            except Exception as e:
                raise ConnectionError(f'Read error: {e}')

            if len(hdr) < 16 or hdr[:4] != b'DBI0':
                continue

            cmd_id    = struct.unpack('<I', hdr[8:12])[0]
            data_size = struct.unpack('<I', hdr[12:16])[0]

            if cmd_id == CommandID.LIST:
                titles = send_list(ctx)
            elif cmd_id == CommandID.FILE_RANGE:
                if titles is None:
                    log.warning('FILE_RANGE diterima sebelum LIST, abaikan')
                    break
                send_file_range(ctx, data_size, titles)
            elif cmd_id == CommandID.EXIT:
                ctx.write(struct.pack('<4sIII', b'DBI0', CommandType.RESPONSE, CommandID.EXIT, 0))
                # Mark any files that DBI processed but didn't trigger end_offset >= fsize
                if titles:
                    for fname in titles:
                        if fname not in _completed_files:
                            log.info(f'EXIT: marking remaining file as done: {fname}')
                            _progress_track.pop(fname, None)
                            _completed_files.add(fname)
                            mark_done(fname)
                log.info(f'{name}: DBI kirim EXIT, transfer selesai')
                break
            else:
                log.warning(f'{name}: Unknown cmd {cmd_id}')
                break

    except Exception as e:
        log.error(f'{name}: {e}')
    finally:
        if ctx:
            ctx.close()
    log.info(f'{name}: Session ended')


def wait_for_switch():
    log.info(f'Menunggu Switch... (serial={SWITCH_SERIAL or "any"})')
    if SWITCH_BUS and SWITCH_DEV:
        log.info(f'Target: bus={SWITCH_BUS} dev={SWITCH_DEV}')

    while True:
        devs = list(usb.core.find(
            find_all=True, idVendor=SWITCH_VID, idProduct=SWITCH_PID
        ) or [])

        if SWITCH_BUS and SWITCH_DEV:
            devs = [d for d in devs if d.bus == int(SWITCH_BUS) and d.address == int(SWITCH_DEV)]

        if devs:
            log.info(f'Switch ditemukan: bus={devs[0].bus} dev={devs[0].address}')
            return devs[0]
        time.sleep(1)


def main():
    log.info(f'dbibackend started — games={WORK_DIR} serial={SWITCH_SERIAL or "any"} server={SERVER_URL}')
    dev = wait_for_switch()
    handle_switch(dev)
    log.info('Switch disconnected/done, container exit.')
    sys.exit(0)


if __name__ == '__main__':
    main()
