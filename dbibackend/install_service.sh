#!/bin/sh
# install_service.sh — Setup usb-monitor sebagai systemd service
# Jalankan sebagai root di host NAS (atau via SSH)
# Usage: sh install_service.sh [GAMES_DIR] [SERVER_URL]

GAMES_DIR="${1:-/volume1/Switch}"
SERVER_URL="${2:-http://localhost:8080}"

set -e

echo "=== Installing USB Monitor Service ==="
echo "GAMES_DIR  : $GAMES_DIR"
echo "SERVER_URL : $SERVER_URL"
echo ""

# Copy script
cp "$(dirname "$0")/usb_monitor.sh" /usr/local/bin/usb_monitor.sh
chmod +x /usr/local/bin/usb_monitor.sh

# Tulis service file dengan env yang sudah di-set
cat > /etc/systemd/system/usb-monitor.service << EOF
[Unit]
Description=Switch USB Monitor — auto-start dbibackend containers
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/usb_monitor.sh
Restart=on-failure
RestartSec=5
Environment=GAMES_DIR=$GAMES_DIR
Environment=QUEUE_DIR=/tmp
Environment=SERVER_URL=$SERVER_URL
StandardOutput=journal
StandardError=journal
KillMode=process
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable usb-monitor
systemctl start  usb-monitor

echo ""
echo "=== Done! ==="
echo "Status  : systemctl status usb-monitor"
echo "Log     : journalctl -u usb-monitor -f"
echo "Restart : systemctl restart usb-monitor"
