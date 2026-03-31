/**
 * app.js — Switch Install Server (USB only)
 */

// ─── State ────────────────────────────────────────────────────────────────────
const state = {
  filtered:        [],
  marked:          new Map(),
  markedFolders:   new Set(),
  folderContents:  new Map(),   // path → [{filepath,filename,filesize}]
  activeIdx:     0,
  currentPath:   "",
  browserItems:  [],
  hyperList:     null,
};

const usbState = {
  switches:       [],
  selectedSerial: null,
  pollTimer:      null,
};

// ─── Utils ────────────────────────────────────────────────────────────────────

function fmtSize(bytes) {
  if (!bytes) return '—';
  if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(2) + " GB";
  if (bytes >= 1048576)    return (bytes / 1048576).toFixed(1) + " MB";
  return (bytes / 1024).toFixed(0) + " KB";
}

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  return res.json();
}

// ─── Tabs ─────────────────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    if (tab.dataset.tab === "usb") {
      loadUsbSwitches();
      startUsbPolling();
    } else {
      stopUsbPolling();
    }
    if (tab.dataset.tab === "settings") loadSettings();
  });
});

// ─── Library / Folder Browser ─────────────────────────────────────────────────

async function browseTo(path) {
  const url = "/api/library/browse" + (path ? "?path=" + encodeURIComponent(path) : "");
  const data = await api(url);
  if (data.error) { toast(data.error, "error"); return; }
  state.currentPath  = data.path  || "";
  state._parentPath  = data.parent || null;
  state.browserItems = data.items  || [];
  applyFilter();
  document.getElementById("lib-path").textContent = state.currentPath || "Root";
}

async function loadLibrary() {
  state.marked.clear();
  state.markedFolders.clear();
  await browseTo("");
}

function applyFilter() {
  const q = document.getElementById("lib-search").value.trim().toLowerCase();
  if (q) {
    state.filtered = state.browserItems.filter(i => i.name.toLowerCase().includes(q));
  } else {
    const items = [...state.browserItems];
    if (state._parentPath !== null && state._parentPath !== undefined) {
      items.unshift({ type: "parent", name: "[ .. ]", path: state._parentPath });
    }
    state.filtered = items;
  }
  state.activeIdx = 0;
  renderList();
  updateStatusBar();
}

// ─── HyperList Virtual Scroll ─────────────────────────────────────────────────

function scrollToActive(container, itemH) {
  const idx = state.activeIdx;
  const top = idx * itemH;
  const visible = container.clientHeight;
  const scrollTop = container.scrollTop;
  if (top < scrollTop) {
    container.scrollTop = top;
  } else if (top + itemH > scrollTop + visible) {
    container.scrollTop = top + itemH - visible;
  }
}

function renderList() {
  const container = document.getElementById("lib-list");
  const ITEM_H = 22;

  if (state.hyperList) {
    state.hyperList.refresh(container, {
      itemHeight: ITEM_H,
      total: state.filtered.length,
      generate(i) { return makeRow(i); }
    });
    scrollToActive(container, ITEM_H);
    return;
  }
  state.hyperList = HyperList.create(container, {
    itemHeight: ITEM_H,
    total: state.filtered.length,
    generate(i) { return makeRow(i); }
  });
  scrollToActive(container, ITEM_H);
}

function makeRow(i) {
  const item = state.filtered[i];
  const el   = document.createElement("div");
  el.className = "lib-row";
  if (i === state.activeIdx) el.classList.add("active");

  if (item.type === "parent") {
    el.classList.add("folder");
    el.textContent = item.name;
  } else if (item.type === "folder") {
    el.classList.add("folder");
    if (state.markedFolders.has(item.path)) {
      el.classList.add("marked");
      const contents = state.folderContents.get(item.path) || [];
      const totalSize = contents.reduce((s, f) => s + (f.filesize || 0), 0);
      const info = contents.length > 0
        ? ` <span style="font-size:10px;opacity:0.8">[${contents.length} file · ${fmtSize(totalSize)}]</span>`
        : ` <span style="font-size:10px;opacity:0.6">[menghitung...]</span>`;
      el.innerHTML = "▶ " + item.name + info;
    } else {
      el.textContent = "▶ " + item.name;
    }
  } else {
    if (state.marked.has(item.path)) el.classList.add("marked");
    el.innerHTML = `<span class="lib-name">${item.name}</span><span class="lib-size">${fmtSize(item.filesize)}</span>`;
  }

  el.addEventListener("click", () => {
    state.activeIdx = i;
    if (item.type === "parent") {
      browseTo(item.path);
    } else if (item.type === "folder" ) {
      // single click = aktifkan, double click = masuk folder
    }
    renderList();
    document.getElementById("lib-list").focus();
  });

  el.addEventListener("dblclick", () => {
    if (item.type === "folder" || item.type === "parent") browseTo(item.path);
  });

  return el;
}

function toggleMark(i) {
  const item = state.filtered[i];
  if (!item || item.type === "parent") return;

  if (item.type === "folder") {
    if (state.markedFolders.has(item.path)) {
      state.markedFolders.delete(item.path);
      // Hapus semua file dari folder ini di marked
      const contents = state.folderContents.get(item.path) || [];
      contents.forEach(f => state.marked.delete(f.filepath));
      state.folderContents.delete(item.path);
      updateStatusBar(); renderList();
    } else {
      // Fetch rekursif dulu
      api("/api/library/folder-contents", {
        method: "POST",
        body: JSON.stringify({ path: item.path })
      }).then(data => {
        if (data.error) { toast(data.error, "error"); return; }
        state.markedFolders.add(item.path);
        state.folderContents.set(item.path, data.files);
        data.files.forEach(f => state.marked.set(f.filepath, { filepath: f.filepath, filename: f.filename, filesize: f.filesize }));
        updateStatusBar(); renderList();
      });
      return; // jangan updateStatusBar dulu, tunggu fetch selesai
    }
  } else {
    if (state.marked.has(item.path)) {
      state.marked.delete(item.path);
    } else {
      state.marked.set(item.path, { filepath: item.path, filename: item.name, filesize: item.filesize });
    }
  }
  updateStatusBar();
  renderList();
}

function markAll() {
  state.browserItems.filter(i => i.type === "file").forEach(i => {
    state.marked.set(i.path, { filepath: i.path, filename: i.name, filesize: i.filesize });
  });
  updateStatusBar(); renderList();
}

function clearMarks() {
  state.marked.clear();
  state.markedFolders.clear();
  state.folderContents.clear();
  updateStatusBar(); renderList();
}

function updateStatusBar() {
  const total   = state.filtered.filter(i => i.type === "file").length;
  const marked  = state.marked.size;
  const totalBytes = [...state.marked.values()].reduce((s, f) => s + (f.filesize || 0), 0);
  const sizeStr = totalBytes > 0 ? ' &nbsp;·&nbsp; ' + fmtSize(totalBytes) : '';
  document.getElementById("lib-statusbar").innerHTML =
    `${total} file &nbsp;|&nbsp; <span style="color:var(--sel-mark);font-weight:bold">${marked} terpilih${sizeStr}</span>`;
  document.getElementById("dash-status").textContent =
    marked ? `${marked} file dipilih` : "Idle";
}

// ─── Keyboard Navigation ──────────────────────────────────────────────────────

document.getElementById("lib-list").addEventListener("keydown", (e) => {
  const len = state.filtered.length;
  if (!len) return;
  if (e.key === "ArrowDown")  { e.preventDefault(); state.activeIdx = Math.min(state.activeIdx + 1, len - 1); renderList(); }
  if (e.key === "ArrowUp")    { e.preventDefault(); state.activeIdx = Math.max(state.activeIdx - 1, 0); renderList(); }
  if (e.key === "PageDown") {
    e.preventDefault();
    const container = document.getElementById("lib-list");
    const page = Math.max(1, Math.floor(container.clientHeight / 22));
    state.activeIdx = Math.min(state.activeIdx + page, len - 1);
    renderList();
  }
  if (e.key === "PageUp") {
    e.preventDefault();
    const container = document.getElementById("lib-list");
    const page = Math.max(1, Math.floor(container.clientHeight / 22));
    state.activeIdx = Math.max(state.activeIdx - page, 0);
    renderList();
  }
  if (e.key === "Home") { e.preventDefault(); state.activeIdx = 0; renderList(); }
  if (e.key === "End")  { e.preventDefault(); state.activeIdx = len - 1; renderList(); }
  if (e.key === "Insert" || e.key === " ") { e.preventDefault(); toggleMark(state.activeIdx); state.activeIdx = Math.min(state.activeIdx + 1, len - 1); renderList(); }
  if (e.key === "Enter") {
    const item = state.filtered[state.activeIdx];
    if (item && (item.type === "folder" || item.type === "parent")) browseTo(item.path);
  }
});

document.getElementById("lib-search").addEventListener("input", applyFilter);
document.getElementById("lib-search").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    document.getElementById("lib-list").focus();
  }
  if (e.key === "Insert") {
    e.preventDefault();
    if (state.filtered.length > 0) {
      state.activeIdx = 0;
      toggleMark(0);
      // Setelah mark, pindah activeIdx ke item berikutnya
      state.activeIdx = Math.min(1, state.filtered.length - 1);
      renderList();
      // Fokus ke list supaya arrow key langsung jalan
      document.getElementById("lib-list").focus();
    }
  }
  if (e.key === "Escape") {
    document.getElementById("lib-search").value = "";
    applyFilter();
    document.getElementById("lib-list").focus();
  }
});

// Ketik huruf/angka dari mana saja → langsung ke search box
document.addEventListener("keydown", (e) => {
  const search = document.getElementById("lib-search");
  const active = document.activeElement;
  // Skip kalau sedang di input lain atau tombol modifier
  if (active === search) return;
  if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  // Hanya huruf, angka, tanda baca
  if (e.key.length === 1) {
    search.focus();
    // Tidak perlu set value, browser akan append sendiri
  }
  if (e.key === "Escape") {
    search.value = "";
    applyFilter();
    document.getElementById("lib-list").focus();
  }
});

document.getElementById("btn-refresh-lib").addEventListener("click", loadLibrary);

document.getElementById("btn-load-txt").addEventListener("click", () => {
  document.getElementById("file-input-txt").click();
});

document.getElementById("file-input-txt").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  // Detect encoding: UTF-16 LE punya BOM FF FE di awal
  const buffer = await file.arrayBuffer();
  const bytes  = new Uint8Array(buffer);
  let text;
  if (bytes[0] === 0xFF && bytes[1] === 0xFE) {
    text = new TextDecoder("utf-16le").decode(buffer);
  } else if (bytes[0] === 0xFE && bytes[1] === 0xFF) {
    text = new TextDecoder("utf-16be").decode(buffer);
  } else {
    text = new TextDecoder("utf-8").decode(buffer);
  }
  const rawLines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);

  // Baris dengan \ di akhir = folder, sisanya = file
  const folderNames = rawLines.filter(l => l.endsWith("\\")).map(l => l.slice(0, -1).trim().toLowerCase());
  const fileNames   = rawLines.filter(l => !l.endsWith("\\")).map(l => l.toLowerCase());

  // Hapus pilihan lama
  state.marked.clear();
  state.markedFolders.clear();
  state.folderContents.clear();

  let foundFiles   = 0;
  let foundFolders = 0;

  // Match file biasa
  for (const item of state.browserItems) {
    if (item.type !== "file") continue;
    if (fileNames.some(l => item.name.toLowerCase().includes(l))) {
      state.marked.set(item.path, { filepath: item.path, filename: item.name, filesize: item.filesize });
      foundFiles++;
    }
  }

  // Match folder — fetch rekursif
  const folderItems = state.browserItems.filter(item => item.type === "folder");
  const fetches = [];
  for (const item of folderItems) {
    if (folderNames.some(l => item.name.toLowerCase().includes(l) || l.includes(item.name.toLowerCase()))) {
      fetches.push(
        api("/api/library/folder-contents", {
          method: "POST",
          body: JSON.stringify({ path: item.path })
        }).then(data => {
          if (data.error || !data.files) return;
          state.markedFolders.add(item.path);
          state.folderContents.set(item.path, data.files);
          data.files.forEach(f => state.marked.set(f.filepath, { filepath: f.filepath, filename: f.filename, filesize: f.filesize }));
          foundFolders++;
        })
      );
    }
  }

  if (fetches.length > 0) {
    toast("Menghitung isi folder...", "");
    await Promise.all(fetches);
  }

  const total = foundFiles + foundFolders;
  toast(`${foundFiles} file + ${foundFolders} folder ditandai dari .txt`, total ? "success" : "error");
  updateStatusBar(); renderList();
  e.target.value = "";
});

// ─── Resizer ──────────────────────────────────────────────────────────────────

(function initResizer() {
  const resizer = document.getElementById("resizer");
  const left    = document.getElementById("library-panel");
  let dragging  = false;
  let startX, startW;

  resizer.addEventListener("mousedown", e => {
    dragging = true;
    startX   = e.clientX;
    startW   = left.offsetWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", e => {
    if (!dragging) return;
    const newW = Math.max(200, Math.min(startW + (e.clientX - startX), window.innerWidth - 300));
    left.style.width = newW + "px";
    left.style.flex  = "none";
    if (state.hyperList) renderList();
  });

  document.addEventListener("mouseup", () => {
    dragging = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  });
})();

// ─── Settings ─────────────────────────────────────────────────────────────────

async function loadSettings() {
  const data = await api("/api/settings");
  document.getElementById("set-game-folder").value = data.game_folder || "";
}

document.getElementById("btn-browse-folder").addEventListener("click", async () => {
  const current = document.getElementById("set-game-folder").value;
  const data    = await api("/api/settings/browse-folder", {
    method: "POST",
    body: JSON.stringify({ path: current }),
  });
  if (data.path) document.getElementById("set-game-folder").value = data.path;
});

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const folder = document.getElementById("set-game-folder").value.trim();
  await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ game_folder: folder }),
  });
  toast("Settings disimpan", "success");
  if (folder) {
    await loadLibrary();
  }
});

// ─── USB Install ──────────────────────────────────────────────────────────────

async function loadUsbSwitches() {
  const data = await api("/api/usb/switches");
  // Sort: terhubung dulu, lalu tidak terhubung
  usbState.switches = (data || []).sort((a, b) => {
    if (a.connected && !b.connected) return -1;
    if (!a.connected && b.connected) return 1;
    return (a.name || a.serial).localeCompare(b.name || b.serial);
  });
  renderUsbSwitches();
  // Adaptive polling: lebih cepat kalau ada Switch konek + pending/transferring
  const hasActive = usbState.switches.some(sw => sw.connected && (sw.pending > 0 || (sw.queue && sw.queue.some(q => q.status === 'transferring'))));
  if (hasActive) {
    startUsbPolling(5000);    // 5s saat transfer aktif
  } else {
    startUsbPolling(30000);   // 30s saat idle
  }
}

let _pollInterval = 3000;

function startUsbPolling(interval) {
  const ms = interval || _pollInterval;
  // Hanya restart timer kalau interval berubah signifikan
  if (usbState.pollTimer && Math.abs(ms - _pollInterval) < 500) return;
  stopUsbPolling();
  _pollInterval = ms;
  usbState.pollTimer = setInterval(loadUsbSwitches, ms);
}

function stopUsbPolling() {
  if (usbState.pollTimer) {
    clearInterval(usbState.pollTimer);
    usbState.pollTimer = null;
  }
}

function renderUsbSwitches() {
  const el = document.getElementById("usb-switch-list");
  if (!el) return;
  const scrollEl = document.getElementById("tab-usb");
  const scrollTop = scrollEl ? scrollEl.scrollTop : 0;

  if (!usbState.switches.length) {
    el.innerHTML = '<div style="color:var(--text-dim);font-size:12px;padding:12px">Tidak ada Switch terdeteksi. Colok Switch dan buka DBI → Install from DBIbackend.</div>';
    return;
  }

  el.innerHTML = usbState.switches.map(sw => {
    const isSelected = usbState.selectedSerial === sw.serial;
    const dot        = sw.connected ? '●' : '●';
    const dotColor   = sw.connected ? '#4caf50' : '#555';
    const statusText = sw.connected ? 'Terhubung' : 'Tidak terhubung';
    const name       = sw.name || sw.serial;
    const pending    = sw.pending || 0;
    const done       = sw.done || 0;
    const errors     = sw.queue ? sw.queue.filter(q => q.status === 'error').length : 0;
    const total      = sw.queue ? sw.queue.length : (pending + done);

    // Progress bar
    const pct        = total > 0 ? Math.round((done / total) * 100) : 0;
    const progressBar = total > 0 ? `
      <div style="margin-top:6px">
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-dim);margin-bottom:2px">
          <span>${done}/${total} game${errors ? ' · <span style="color:#f44">' + errors + ' error</span>' : ''}</span>
          <span>${pct}%</span>
        </div>
        <div style="background:var(--border);border-radius:2px;height:4px">
          <div style="background:${errors ? '#f44' : pct===100 ? 'var(--green)' : 'var(--accent)'};width:${pct}%;height:4px;border-radius:2px;transition:width 0.3s"></div>
        </div>
      </div>` : '';

    // Queue list — status: pending=⏳ done=✓ error=✗
    const queueHtml = sw.queue && sw.queue.length ? (() => {
      const sortedQueue = [...sw.queue].sort((a, b) => {
        const order = {transferring: 0, pending: 1, done: 2, error: 3};
        return (order[a.status] ?? 1) - (order[b.status] ?? 1);
      });
      const rows = sortedQueue.map(q => {
        const icon  = q.status==='done'        ? '<span style="color:var(--green)">✓</span>'
                    : q.status==='error'        ? '<span style="color:#f44">✗</span>'
                    : q.status==='transferring' ? '<span style="color:var(--accent)">↑</span>'
                    : '<span style="color:var(--text-dim)">⏳</span>';
        const color = q.status==='done' ? 'var(--text-dim)' : q.status==='error' ? '#f44' : 'var(--text)';
        const pct   = q.progress || 0;
        const progressHtml = q.status==='transferring'
          ? '<div style="position:absolute;left:0;top:0;height:100%;width:' + pct + '%;background:rgba(0,85,204,0.12);border-radius:2px;pointer-events:none"></div>'
          : '';
        return '<div style="position:relative;display:flex;align-items:center;gap:6px;padding:2px 0;border-bottom:1px solid var(--border)">'
             + progressHtml
             + icon
             + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:' + color + '">' + q.filename + '</span>'
             + (q.status==='transferring' ? '<span style="color:var(--accent);font-size:10px">' + pct + '%</span>' : '')
             + '<span style="color:var(--text-dim)">' + fmtSize(q.filesize) + '</span>'
             + '</div>';
      }).join('');
      return '<div style="margin-top:6px;font-size:10px;max-height:220px;overflow-y:auto;border:1px solid var(--border);border-radius:2px">' + rows + '</div>';
    })() : '';

    return `
      <div class="usb-sw-card ${isSelected ? 'selected' : ''}" onclick="selectUsbSwitch('${sw.serial}')">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="color:${dotColor}">${dot}</span>
          <span style="font-weight:bold;font-size:13px">${name}</span>
          <span style="font-size:10px;color:var(--text-dim)">${statusText}</span>
          <div class="spacer"></div>
          <button class="btn" style="font-size:10px;padding:2px 8px" onclick="event.stopPropagation();renameUsbSwitch('${sw.serial}','${(sw.name||'').replace(/'/g,"\\'")}')">✏ Nama</button>
          ${total > 0 ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="event.stopPropagation();clearUsbQueue('${sw.serial}')">✕ Clear</button>` : ''}
          ${pending > 0 && sw.connected ? `<button class="btn primary" style="font-size:10px;padding:2px 8px" onclick="event.stopPropagation();resendQueue('${sw.serial}')">↺ Send Ulang</button>` : ''}
          ${!sw.connected ? `<button class="btn danger" style="font-size:10px;padding:2px 8px;color:#c01;border-color:#c01" onclick="event.stopPropagation();deleteUsbSwitch('${sw.serial}')">🗑 Hapus</button>` : ''}
        </div>
        <div style="font-size:10px;color:var(--text-dim);margin-top:3px">
          ${sw.serial}${sw.connected ? ` · Port: ${sw.devpath}` : ''}
        </div>
        ${progressBar}
        ${queueHtml}
      </div>
    `;
  }).join('');
  if (scrollEl) scrollEl.scrollTop = scrollTop;
}

function selectUsbSwitch(serial) {
  usbState.selectedSerial = usbState.selectedSerial === serial ? null : serial;
  renderUsbSwitches();
  document.getElementById("btn-usb-send").disabled = !usbState.selectedSerial;
}

async function resendQueue(serial) {
  // Ambil queue yang masih pending dari DB, kirim ulang ke Switch
  const queue = await api(`/api/usb/switches/${serial}/queue`);
  const pending = queue.filter(q => q.status === 'pending');
  if (!pending.length) {
    toast("Tidak ada yang perlu dikirim ulang", "error");
    return;
  }
  const files = pending.map(q => ({ filepath: q.filepath, filename: q.filename, filesize: q.filesize }));
  await api(`/api/usb/switches/${serial}/queue`, {
    method: "POST",
    body: JSON.stringify({ files })
  });
  toast(`${files.length} game dikirim ulang ke Switch`, "success");
  await loadUsbSwitches();
}

async function deleteUsbSwitch(serial) {
  if (!confirm("Hapus Switch ini dari daftar?")) return;
  await api(`/api/usb/switches/${serial}`, { method: "DELETE" });
  await loadUsbSwitches();
  toast("Switch dihapus", "success");
}

async function renameUsbSwitch(serial, currentName) {
  const name = prompt("Nama Switch:", currentName);
  if (name === null) return;
  await api(`/api/usb/switches/${serial}/name`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  toast("Nama disimpan", "success");
  await loadUsbSwitches();
}

async function clearUsbQueue(serial) {
  if (!confirm("Hapus semua queue untuk Switch ini?")) return;
  await api(`/api/usb/switches/${serial}/queue`, {
    method: "POST",
    body: JSON.stringify({ files: [] }),
  });
  toast("Queue dihapus", "success");
  await loadUsbSwitches();
}

document.getElementById("btn-usb-refresh").addEventListener("click", loadUsbSwitches);

document.getElementById("btn-usb-send").addEventListener("click", async () => {
  const serial = usbState.selectedSerial;
  if (!serial) return;

  const files = [...state.marked.values()];
  if (!files.length) {
    toast("Pilih game dulu dari library (tekan Insert/Spasi)", "error");
    return;
  }

  const sw   = usbState.switches.find(s => s.serial === serial);
  const name = sw?.name || serial;

  const res = await api(`/api/usb/switches/${serial}/queue`, {
    method: "POST",
    body: JSON.stringify({ files }),
  });

  if (res.ok) {
    toast(`${files.length} game → ${name}`, "success");
    clearMarks();
    await loadUsbSwitches();
  } else {
    toast("Gagal kirim queue", "error");
  }
});

// ─── Command bar shortcuts ────────────────────────────────────────────────────

document.getElementById("cmd-ins").addEventListener("click",      () => { toggleMark(state.activeIdx); });
document.getElementById("cmd-space").addEventListener("click",    () => { toggleMark(state.activeIdx); });
document.getElementById("cmd-selall").addEventListener("click",   markAll);
document.getElementById("cmd-selclear").addEventListener("click", clearMarks);
document.getElementById("cmd-loadtxt").addEventListener("click",  () => document.getElementById("file-input-txt").click());

// ─── Init ─────────────────────────────────────────────────────────────────────

(async function init() {
  await loadSettings();
  await loadLibrary();
  await loadUsbSwitches();
  startUsbPolling();
  document.getElementById("lib-search").focus();
})();
