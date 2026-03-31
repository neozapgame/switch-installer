#!/bin/sh
# usb_monitor.sh — Auto start/stop dbibackend container per Switch
# Dirancang untuk dijalankan sebagai systemd service (single instance)

GAMES_DIR="${GAMES_DIR:-/volume1/Switch}"
QUEUE_DIR="${QUEUE_DIR:-/tmp}"
SERVER_URL="${SERVER_URL:-http://172.17.0.1:8080}"
IMAGE="dbibackend"
POLL=2
LOCKFILE="/var/run/usb_monitor.lock"
LOGFILE="/var/log/usb_monitor.log"

log() {
    msg="$(date '+%Y-%m-%d %H:%M:%S') $*"
    echo "$msg"
    echo "$msg" >> "$LOGFILE" 2>/dev/null || true
}

acquire_lock() {
    if [ -f "$LOCKFILE" ]; then
        old_pid=$(cat "$LOCKFILE" 2>/dev/null)
        if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
            log "ERROR: usb_monitor sudah jalan (PID $old_pid). Exit."
            exit 1
        else
            log "Lockfile lama ditemukan (PID $old_pid sudah mati), dilanjutkan."
            rm -f "$LOCKFILE"
        fi
    fi
    echo $$ > "$LOCKFILE"
    log "Lock acquired (PID $$)"
}

release_lock() {
    rm -f "$LOCKFILE"
    log "Lock released"
}

cleanup() {
    log "Signal diterima, shutdown..."
    docker ps --filter 'name=dbi-' --format '{{.Names}}' | while read -r name; do
        log "Stopping container $name (shutdown)"
        docker stop "$name" 2>/dev/null
        docker rm   "$name" 2>/dev/null
    done
    release_lock
    exit 0
}

trap cleanup INT TERM

scan_switches() {
    for d in /sys/bus/usb/devices/*/; do
        [ -f "${d}idVendor" ] || continue
        vid=$(cat "${d}idVendor" 2>/dev/null | tr -d ' \n' | tr 'ABCDEF' 'abcdef')
        pid=$(cat "${d}idProduct" 2>/dev/null | tr -d ' \n' | tr 'ABCDEF' 'abcdef')
        [ "$vid" = "057e" ] && [ "$pid" = "3000" ] || continue
        serial=$(cat "${d}serial" 2>/dev/null | tr -d ' \n')
        [ -z "$serial" ] && continue
        busnum=$(cat "${d}busnum" 2>/dev/null | tr -d ' \n')
        devnum=$(cat "${d}devnum" 2>/dev/null | tr -d ' \n')
        devpath=$(basename "$d")
        printf '%s:%s:%s:%s\n' "$serial" "$devpath" "$busnum" "$devnum"
    done
}

container_name() {
    echo "dbi-$(echo "$1" | tr -dc 'a-zA-Z0-9' | cut -c1-12)"
}

start_container() {
    serial="$1"; busnum="$2"; devnum="$3"
    name=$(container_name "$serial")
    devnum_pad=$(printf '%03d' "$devnum")

    if docker ps -a --filter "name=^${name}$" --format '{{.Names}}' | grep -q "^${name}$"; then
        log "Stopping old container $name"
        docker stop "$name" 2>/dev/null
        docker rm   "$name" 2>/dev/null
    fi

    log "Starting $name (serial=$serial bus=$busnum dev=$devnum_pad)"
    docker run -d \
        --name "$name" \
        --restart no \
        --device "/dev/bus/usb/$(printf '%03d' "$busnum")/$devnum_pad" \
        -v "$GAMES_DIR:/games:ro" \
        -v "$QUEUE_DIR:/tmp" \
        -e TZ=Asia/Jakarta \
        -e SWITCH_BUS="$busnum" \
        -e SWITCH_DEV="$devnum" \
        -e SWITCH_SERIAL="$serial" \
        -e QUEUE_DIR=/tmp \
        -e SERVER_URL="$SERVER_URL" \
        "$IMAGE" > /dev/null 2>&1 && \
        log "Container $name started OK" || \
        log "ERROR: Container $name gagal start"
}

stop_container() {
    serial="$1"
    name=$(container_name "$serial")
    docker stop "$name" 2>/dev/null
    docker rm   "$name" 2>/dev/null
    log "Container $name stopped (serial=$serial)"
}

acquire_lock
log "USB Monitor started — GAMES_DIR=$GAMES_DIR SERVER_URL=$SERVER_URL"

prev_state=""

while true; do
    current=""

    while IFS=: read -r serial devpath busnum devnum; do
        [ -z "$serial" ] && continue
        current="${current}${serial}:${busnum}:${devnum}\n"

        prev_entry=$(printf '%b' "$prev_state" | grep "^${serial}:")
        if [ -z "$prev_entry" ]; then
            log "Switch baru: serial=$serial port=$devpath bus=$busnum dev=$devnum"
            start_container "$serial" "$busnum" "$devnum"
        else
            prev_dev=$(echo "$prev_entry" | cut -d: -f3)
            if [ "$prev_dev" != "$devnum" ]; then
                log "Switch reconnect: serial=$serial devnum $prev_dev->$devnum"
                start_container "$serial" "$busnum" "$devnum"
            else
                # Health check — restart hanya kalau container crash (exit code != 0)
                # Kalau exit 0 = selesai normal, tidak perlu restart
                name=$(container_name "$serial")
                if ! docker ps --filter "name=^${name}$" --format '{{.Names}}' | grep -q "^${name}$"; then
                    exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$name" 2>/dev/null)
                    if [ "$exit_code" = "1" ] || [ "$exit_code" = "2" ]; then
                        log "Container $name crash (exit=$exit_code), restart..."
                        start_container "$serial" "$busnum" "$devnum"
                    else
                        log "Container $name selesai normal (exit=$exit_code), menunggu DBI..."
                    fi
                fi
            fi
        fi
    done << EOF
$(scan_switches)
EOF

    if [ -n "$prev_state" ]; then
        printf '%b' "$prev_state" | grep -v '^$' | while IFS=: read -r serial busnum devnum; do
            [ -z "$serial" ] && continue
            still_here=$(scan_switches | grep "^${serial}:")
            if [ -z "$still_here" ]; then
                log "Switch dicabut: serial=$serial"
                stop_container "$serial"
            fi
        done
    fi

    prev_state=$(printf '%b' "$current")
    sleep $POLL
done
