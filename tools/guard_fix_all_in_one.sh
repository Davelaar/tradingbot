#!/usr/bin/env bash
# Fix ALLE guards in één run, veilig en stil.
set -Eeuo pipefail

QUIET="${QUIET:-1}"           # 1 = weinig output
WAIT_HTTP_SEC="${WAIT_HTTP_SEC:-10}"
RECON_URL="http://127.0.0.1:9111/metrics"
MUX_URL="http://127.0.0.1:9120/metrics"

log(){ [ "$QUIET" = "1" ] && return 0; echo -e "$@"; }

read_map() {
  curl -s "$RECON_URL" \
   | sed -n 's/^guard_port_assignment{market="\([^"]\+\)"} \([0-9][0-9]*\).*/\1 \2/p' \
   | sort -u
}

read_port_for() {
  local m="$1"
  curl -s "$RECON_URL" \
   | sed -n "s/^guard_port_assignment{market=\"${m}\"} \([0-9][0-9]*\).*/\1/p" \
   | tail -n1
}

wait_http_200() {
  local p="$1" code="000" i=0 max=$((WAIT_HTTP_SEC*3))
  while [ $i -lt $max ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${p}/metrics" || true)
    [ "$code" = "200" ] && echo "200" && return 0
    sleep 0.33; i=$((i+1))
  done
  echo "$code"; return 1
}

fix_one() {
  local M="$1" MP="$2"
  [ -z "$MP" ] && { echo "[${M}] geen mapping-poort"; return 1; }
  echo "-> ${M} (${MP})"

  # env ↔ mapping gelijk trekken
  echo "PROM_PORT=${MP}" | tee "/etc/trading/guard/${M}.env" >/dev/null

  # Stop + reset
  systemctl stop "trading-guard@${M}.service" 2>/dev/null || true
  systemctl reset-failed "trading-guard@${M}.service" 2>/dev/null || true
  sleep 0.2

  # Locks gericht verwijderen (zonder redis-cli)
  MARKET="$M" sudo -u trader bash -lc 'MARKET="$MARKET" /srv/trading/.venv/bin/python - <<PY
import os
from redis import Redis
m=os.environ["MARKET"]
r=Redis.from_url("redis://127.0.0.1:6379/0",decode_responses=True)
keys=set([f"lock:guard:{m}"])
keys |= set(r.keys(f"*{m}*lock*"))
keys |= set(r.keys(f"guard:*:{m}*lock*"))
for k in sorted(keys):
    if r.delete(k): print("DEL",k)
print("DONE")
PY' >/dev/null || true

  # Listener precies op die poort vrijmaken
  PIDS=$(ss -ltnp 2>/dev/null | awk -v P=":${MP} " '$0 ~ P {print $NF}' \
        | sed -E 's/.*pid=([0-9]+).*/\1/' | sort -u || true)
  [ -n "${PIDS:-}" ] && for pid in $PIDS; do kill "$pid" 2>/dev/null || true; done

  # Start + wacht op 200 + korte metrics sanity
  systemctl start "trading-guard@${M}.service"
  local code; code=$(wait_http_200 "$MP" || true)
  printf "   %s :%s -> %s\n" "$M" "$MP" "$code"
  [ "$code" = "200" ] || return 1

  # (optioneel) één guard-metric tonen bij verbose
  log "$(curl -s "http://127.0.0.1:${MP}/metrics" | grep -E '^(guard_ready|guard_errors_total)' | head -n 3)"
}

echo "== [ALL] Pauzeer reconciler =="
systemctl stop trading-guard-reconciler.service || true
sleep 0.5

# Mapping ophalen; indien leeg: reconciler 1s aan om te vullen
MAP="$(read_map || true)"
if [ -z "$MAP" ]; then
  systemctl start trading-guard-reconciler.service || true
  sleep 1
  MAP="$(read_map || true)"
  systemctl stop trading-guard-reconciler.service || true
fi
[ -z "$MAP" ] && { echo "!! Geen mapping uit reconciler. Afbreken."; exit 1; }

echo "== [ALL] Start sequentiële fix =="
echo "$MAP" | while read -r MKT PORT; do
  fix_one "$MKT" "$PORT" || echo "   WARN: ${MKT} niet 200"
done

echo "== [ALL] Reconciler weer aan =="
systemctl start trading-guard-reconciler.service
sleep 1

echo "== [ALL] Port-sanity (verwacht 200 overal) =="
PORTS=$(read_map | awk '{print $2}' | sort -nu)
for p in $PORTS; do printf ":%s -> " "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$p/metrics"; done

echo "== [ALL] MUX-sanity =="
curl -s "$MUX_URL" | grep -E '^(guard_mux_targets|guard_mux_scrape_errors_total\{)' || true

echo "== [ALL] Klaar =="
