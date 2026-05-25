#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  GUCCI QUANT — Systemd Setup
#  Replaces screen with proper OS-level services.
#  Auto-starts on boot, auto-restarts on crash.
#
#  Usage (run once on VPS): bash ~/GucciQuant/setup_systemd.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

PYTHON="/root/GucciQuant/venv/bin/python3"
DIR="/root/GucciQuant"

echo ""
echo "  ⚙️  Installing GUCCI QUANT as systemd services..."
echo ""

# ── Bot service ──────────────────────────────────────────────────────────────
cat > /etc/systemd/system/gucci-bot.service << EOF
[Unit]
Description=GUCCI QUANT — Funding Rate Arbitrage Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${DIR}
ExecStart=${PYTHON} -u main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gucci-bot
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# ── Dashboard service ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/gucci-dashboard.service << EOF
[Unit]
Description=GUCCI QUANT — Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${DIR}
ExecStart=${PYTHON} dashboard.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gucci-dashboard

[Install]
WantedBy=multi-user.target
EOF

# ── Enable + start ────────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable gucci-bot gucci-dashboard
systemctl restart gucci-bot gucci-dashboard

sleep 3

echo ""
echo "  ┌──────────────────────────────────────────────────────┐"
echo "  │  ✅  Services installed and running                  │"
echo "  │                                                      │"
echo "  │  Dashboard:  http://187.124.41.102:8080              │"
echo "  │                                                      │"
echo "  │  Useful commands:                                    │"
echo "  │  systemctl status gucci-bot                          │"
echo "  │  journalctl -u gucci-bot -f        (live logs)       │"
echo "  │  systemctl restart gucci-bot                         │"
echo "  │  systemctl stop gucci-bot                            │"
echo "  └──────────────────────────────────────────────────────┘"
echo ""
