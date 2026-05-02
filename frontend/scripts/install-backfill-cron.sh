#!/usr/bin/env bash
# Install the backfill cron job on the VPS.
# Retries any PDFs stuck in /mnt/uploads/complete/ every 30 minutes.
#
# Prerequisites:
#   - /usr/local/bin/backfill_contributed.py installed
#   - python3-requests installed
#   - ADMIN_INGEST_TOKEN in /root/.backfill.env (sourced by cron)
#
# Usage: sudo bash scripts/install-backfill-cron.sh

set -euo pipefail

SCRIPT="/usr/local/bin/backfill_contributed.py"
ENV_FILE="/root/.backfill.env"
LOG="/var/log/backfill-uploads.log"
CRON_FILE="/etc/cron.d/munin-backfill"

# Ensure script is installed
if [ ! -f "$SCRIPT" ]; then
    echo "ERROR: $SCRIPT not found. Install it first:"
    echo "  sudo cp scripts/backfill_contributed.py /usr/local/bin/"
    echo "  sudo chmod +x /usr/local/bin/backfill_contributed.py"
    exit 1
fi

# Ensure env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE — fill in the token:"
    echo 'ADMIN_INGEST_TOKEN=' > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "ERROR: Set ADMIN_INGEST_TOKEN in $ENV_FILE before continuing."
    exit 1
fi

# Ensure log file
touch "$LOG"
chown root:adm "$LOG"
chmod 664 "$LOG"

# Install cron
cat > "$CRON_FILE" <<'EOF'
# Retry failed/pending cluster ingests every 30 minutes
SHELL=/bin/bash
*/30 * * * * root . /root/.backfill.env && /usr/local/bin/backfill_contributed.py --all >> /var/log/backfill-uploads.log 2>&1
EOF

chmod 644 "$CRON_FILE"
echo "Cron installed at $CRON_FILE"
echo "Runs every 30 min, logs to $LOG"
