"""
scanner.py — Browse folder game langsung dari filesystem.
Tidak ada DB cache — baca langsung setiap request.
"""

import os
from pathlib import Path

SUPPORTED_EXTENSIONS = {'.nsp', '.nsz', '.xci', '.xcz'}


def browse_folder(path: str, root_folder: str) -> dict:
    """
    List isi satu folder: subfolder + file game.
    """
    if not path or not os.path.isdir(path):
        path = root_folder

    path        = os.path.normpath(path)
    root_folder = os.path.normpath(root_folder)

    parent = None if path == root_folder else os.path.dirname(path)

    items = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        entries = []

    for entry in entries:
        if entry.name.startswith('.'):
            continue
        if entry.is_dir(follow_symlinks=False):
            items.append({
                'type': 'folder',
                'name': entry.name,
                'path': entry.path,
            })
        elif entry.is_file(follow_symlinks=False):
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            try:
                filesize = entry.stat().st_size
            except OSError:
                filesize = 0
            items.append({
                'type':     'file',
                'name':     entry.name,
                'path':     entry.path,
                'filesize': filesize,
            })

    return {'path': path, 'parent': parent, 'items': items}


def resolve_folder(folder_path: str) -> list[dict]:
    """
    Ambil semua file game di dalam folder_path (1 level).
    Return list of {filename, filepath, filesize}
    """
    if not folder_path or not os.path.isdir(folder_path):
        return []

    result = []
    try:
        for entry in os.scandir(folder_path):
            if not entry.is_file(follow_symlinks=False):
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            try:
                filesize = entry.stat().st_size
            except OSError:
                filesize = 0
            result.append({
                'filename': entry.name,
                'filepath': entry.path,
                'filesize': filesize,
            })
    except OSError:
        pass

    return sorted(result, key=lambda x: x['filename'].lower())


def scan_folder_recursive(folder_path: str) -> list[dict]:
    """
    Ambil semua file game di dalam folder_path secara rekursif.
    Return list of {filename, filepath, filesize}
    """
    if not folder_path or not os.path.isdir(folder_path):
        return []

    result = []
    for dirpath, _, files in os.walk(folder_path):
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                filesize = os.path.getsize(fpath)
            except OSError:
                filesize = 0
            result.append({
                'filename': fname,
                'filepath': fpath,
                'filesize': filesize,
            })
    return result
