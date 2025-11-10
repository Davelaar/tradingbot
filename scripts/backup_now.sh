#!/usr/bin/env bash
set -Eeuo pipefail
BASE="/srv/trading"
DST="$BASE/backups"
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DST"/{compose,storage,logs}

# 1) storage (redis uitgesloten)
timeout 180 tar -C "$BASE" \
  --exclude=storage/redis --exclude=storage/redis/** \
  --ignore-failed-read \
  -czf "$DST/storage/storage-$TS.tgz" storage || true

# 2) filebrowser (optioneel)
if [ -d "$BASE/filebrowser" ]; then
  timeout 120 tar -C "$BASE" --ignore-failed-read -czf "$DST/storage/filebrowser-$TS.tgz" filebrowser || true
fi

# 3) compose
timeout 120 tar -C "$BASE" --ignore-failed-read -czf "$DST/compose/compose-$TS.tgz" compose || true

# 4) docker logs (optioneel)
if [ -d /var/lib/docker/containers ]; then
  timeout 300 tar -C / --ignore-failed-read -czf "$DST/logs/docker-logs-$TS.tgz" var/lib/docker/containers || true
fi

# 5) rotatie (14 dagen)
find "$DST" -type f -mtime +14 -delete
