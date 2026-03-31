"""
server.py — Flask app untuk Switch Install Server (USB only, no FTP).
"""

import os
import pathlib
from flask import Flask, jsonify, request, send_from_directory
import database as db
import scanner

# In-memory progress store: {serial: {filename: progress_pct}}
_progress: dict = {}
# In-memory done store for files not in current DB queue: {serial: set(filename)}
_done_files: dict = {}

app = Flask(__name__, static_folder="static")
db.init_db()
db.init_usb_switches_table()

GAMES_DIR = os.environ.get('GAMES_DIR', '/games')


# ─── Static ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ─── Settings ────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({
        "game_folder": db.get_setting("game_folder", GAMES_DIR),
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.json or {}
    if "game_folder" in data:
        db.set_setting("game_folder", data["game_folder"])
    return jsonify({"ok": True})


@app.route("/api/settings/browse-folder", methods=["POST"])
def browse_root_folder():
    data   = request.json or {}
    path   = data.get("path", "")
    result = scanner.browse_folder(path, "/")
    return jsonify(result)


# ─── Library ─────────────────────────────────────────────────────────────────

@app.route("/api/library/browse")
def library_browse():
    game_folder = db.get_setting("game_folder", GAMES_DIR)
    path        = request.args.get("path", "")
    result      = scanner.browse_folder(path or game_folder, game_folder)
    return jsonify(result)


# ─── USB Switches ────────────────────────────────────────────────────────────

@app.route("/api/usb/switches", methods=["GET"])
def get_usb_switches():
    switches  = db.get_all_usb_switches()
    connected = _scan_usb_switches()

    for sw in switches:
        conn = connected.get(sw['serial'])
        sw['connected'] = conn is not None
        sw['devpath']   = conn['devpath'] if conn else ''
        sw['busnum']    = conn['busnum']  if conn else 0
        sw['devnum']    = conn['devnum']  if conn else 0
        queue = db.get_usb_queue(sw['serial'])
        sw_progress = _progress.get(sw['serial'], {})
        queue_list = []
        queue_filenames = set()
        for q in queue:
            qd = dict(q)
            queue_filenames.add(q['filename'])
            if q['status'] == 'pending' and q['filename'] in sw_progress:
                qd['status']   = 'transferring'
                qd['progress'] = sw_progress[q['filename']]
            queue_list.append(qd)
        # Include actively-transferring files not in current DB queue
        for fname, pct in sw_progress.items():
            if fname not in queue_filenames:
                queue_list.insert(0, {
                    'filename': fname,
                    'filepath': '',
                    'filesize': 0,
                    'status':   'transferring',
                    'progress': pct,
                })
        # Include done files not in current DB queue
        sw_done_files = _done_files.get(sw['serial'], set())
        for fname in sw_done_files:
            if fname not in queue_filenames:
                queue_list.insert(0, {
                    'filename': fname,
                    'filepath': '',
                    'filesize': 0,
                    'status':   'done',
                })
        sw['queue']   = queue_list
        sw['pending'] = sum(1 for q in queue if q['status'] == 'pending')
        sw['done']    = sum(1 for q in queue if q['status'] == 'done') + \
                        sum(1 for f in sw_done_files if f not in queue_filenames)

    # Tambah Switch yang konek tapi belum terdaftar
    registered = {sw['serial'] for sw in switches}
    for serial, info in connected.items():
        if serial not in registered:
            db.upsert_usb_switch(serial)
            switches.append({
                'serial':    serial,
                'name':      '',
                'connected': True,
                'devpath':   info['devpath'],
                'busnum':    info['busnum'],
                'devnum':    info['devnum'],
                'pending':   0,
                'done':      0,
            })

    return jsonify(switches)


@app.route("/api/usb/switches/<serial>/name", methods=["POST"])
def set_usb_switch_name(serial):
    data = request.json or {}
    db.update_usb_switch_name(serial, data.get('name', '').strip())
    return jsonify({"ok": True})


@app.route("/api/usb/switches/<serial>", methods=["DELETE"])
def delete_usb_switch(serial):
    db.delete_usb_switch(serial)
    return jsonify({"ok": True})


@app.route("/api/usb/switches/<serial>/queue", methods=["GET"])
def get_usb_queue(serial):
    return jsonify(db.get_usb_queue(serial))


@app.route("/api/usb/switches/<serial>/queue", methods=["POST"])
def set_usb_queue(serial):
    data  = request.json or {}
    files = data.get('files', [])
    db.upsert_usb_switch(serial)
    db.set_usb_queue(serial, files)
    _write_queue_file(serial)
    # Reset progress dan done tracking saat queue baru di-set
    _progress.pop(serial, None)
    _done_files.pop(serial, None)
    return jsonify({"ok": True, "queued": len(files)})


@app.route("/api/usb/connected", methods=["GET"])
def get_usb_connected():
    return jsonify(list(_scan_usb_switches().values()))


def _scan_usb_switches() -> dict:
    result = {}
    base   = pathlib.Path('/sys/bus/usb/devices')
    if not base.exists():
        return result
    for d in base.iterdir():
        try:
            vid = (d / 'idVendor').read_text().strip().lower()
            pid = (d / 'idProduct').read_text().strip().lower()
            if vid != '057e' or pid != '3000':
                continue
            serial  = (d / 'serial').read_text().strip()
            busnum  = int((d / 'busnum').read_text().strip())
            devnum  = int((d / 'devnum').read_text().strip())
            devpath = d.name
            result[serial] = {
                'serial':  serial,
                'devpath': devpath,
                'busnum':  busnum,
                'devnum':  devnum,
            }
        except (FileNotFoundError, ValueError):
            pass
    return result


def _write_queue_file(serial: str) -> None:
    import json
    queue   = db.get_usb_queue(serial)
    pending = [q for q in queue if q['status'] == 'pending']
    path    = pathlib.Path(f'/tmp/queue_{serial}.json')
    path.write_text(json.dumps(pending, ensure_ascii=False))




@app.route("/api/library/folder-contents", methods=["POST"])
def folder_contents():
    """Hitung semua file game di dalam folder secara rekursif."""
    data = request.json or {}
    path = data.get("path", "")
    game_folder = db.get_setting("game_folder", GAMES_DIR)
    # Security: path harus di dalam game_folder
    import os
    path = os.path.normpath(path)
    game_folder = os.path.normpath(game_folder)
    if not path.startswith(game_folder):
        return jsonify({"error": "Invalid path"}), 400
    files = scanner.scan_folder_recursive(path)
    total_size = sum(f['filesize'] for f in files)
    return jsonify({"files": files, "count": len(files), "total_size": total_size})


@app.route("/api/usb/switches/<serial>/notify", methods=["POST"])
def notify_usb_done(serial):
    """
    Dipanggil oleh dbibackend container saat satu game selesai transfer.
    Update status di DB supaya dashboard bisa refresh.
    Body: {"filename": "...", "status": "done"|"error"}
    """
    data     = request.json or {}
    filename = data.get("filename", "")
    status   = data.get("status", "done")

    if not filename:
        return jsonify({"ok": False, "error": "filename required"}), 400

    progress = data.get("progress", None)

    if status == "done":
        db.mark_usb_game_done(serial, filename)
        if serial in _progress:
            _progress[serial].pop(filename, None)
        # Track done files in memory (for files not in current DB queue)
        if serial not in _done_files:
            _done_files[serial] = set()
        _done_files[serial].add(filename)
        _write_queue_file(serial)
    elif status == "transferring" and progress is not None:
        if serial not in _progress:
            _progress[serial] = {}
        _progress[serial][filename] = progress

    return jsonify({"ok": True, "serial": serial, "filename": filename, "status": status})

# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    print(f"Switch Install Server jalan di http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


# ─── Notify dari dbibackend (real-time update) ────────────────────────────────

