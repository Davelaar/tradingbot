#!/usr/bin/env bash
set -Eeuo pipefail
BASE="/srv/trading"
SRC="$BASE/backups/storage"
LATEST=$(ls -t "$SRC"/storage-*.tgz | head -n1)
echo "Herstellen vanaf: $LATEST"
cd "$BASE"
tar -xzf "$LATEST"
echo "Herstel voltooid."
