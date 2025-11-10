#!/usr/bin/env bash
set -Eeuo pipefail
echo "[timestamp] $(date)"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "observability|trading|redis" || true
df -h | grep -E "/srv/trading|Filesystem" || true
echo "Healthcheck voltooid."
