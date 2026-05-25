#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  GUCCI QUANT — Daily DB Backup
#  Keeps 7 rolling snapshots of the SQLite database.
#
#  Install (run once on VPS):
#    chmod +x ~/GucciQuant/backup_db.sh
#    crontab -e
#    # Add: 0 0 * * * /root/GucciQuant/backup_db.sh >> /root/GucciQuant/data/backup.log 2>&1
# ─────────────────────────────────────────────────────────────────────────────
set -e

DB_DIR="/root/GucciQuant/data"
DB_FILE="${DB_DIR}/gucci_quant.db"
BACKUP="${DB_DIR}/gucci_quant.db.bak.$(date +%Y%m%d)"

if [ ! -f "$DB_FILE" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] DB not found — nothing to back up"
    exit 0
fi

cp "$DB_FILE" "$BACKUP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup written: $BACKUP"

# Prune — keep only the 7 most recent backups
ls -t "${DB_DIR}"/gucci_quant.db.bak.* 2>/dev/null | tail -n +8 | xargs rm -f
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Old backups pruned (kept 7)"
