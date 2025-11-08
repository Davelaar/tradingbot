#!/usr/bin/env bash
# Quiet, robust fix for a single guard
set -Eeuo pipefail
MARKET="${1:-HONEY-EUR}"
QUIET="${QUIET:-1}" # 1 = weinig output

log(){ [ "${QUIET}" = "1" ] && return 0; echo "$@"; }

# 0) Port bepalen (reconciler mag hiervoor even draaien)
read_port(){
  curl -s 127.0.0.1:9111/metrics \
   | sed -n "s/^guard_port_assignment{market=\"${MARKET}\"} \([0-9][0-9]*\).*/\1/p" \
   | tail -n1
}
PORT="$(read_port || true)"
if [[ -z "${PORT:-}" || "${PORT}" = "0" ]]; then
  systemctl start trading-guard-reconciler.service >/dev/null 2>&1 || true
  sleep 0.5
  PORT="$(read_port || true)"
fi
# Fallback: env
if [[ -z "${PORT:-}" || "${PORT}" = "0" ]]; then
  PORT="$(awk -F= '/^PROM_PORT=/{print $2}' "/etc/trading/guard/${MARKET}.env" 2>/dev/null || true)"
fi
[[ -z "${PORT:-}" || "${PORT}" = "0" ]] && { echo "[fix-one] geen poort voor ${MARKET}"; exit 2; }
log "[fix-one] ${MARKET} -> :${PORT}"

# 1) Pauzeer reconciler (voorkom remap tijdens herstel)
systemctl stop trading-guard-reconciler.service >/dev/null 2>&1 || true
sleep 0.2

# 2) Stop unit & reset failed
systemctl stop "trading-guard@${MARKET}.service" >/dev/null 2>&1 || true
systemctl reset-failed "trading-guard@${MARKET}.service" >/dev/null 2>&1 || true

# 3) Env sync
install -d -m 0755 /etc/trading/guard
printf 'PROM_PORT=%s\n' "${PORT}" > "/etc/trading/guard/${MARKET}.env"

# 4) Luisteraar op poort netjes vrijmaken (alleen exact die poort)
PIDS=$(ss -ltnp 2>/dev/null | awk -v P=":${PORT} " '$0 ~ P {print $NF}' \
       | sed -E 's/.*pid=([0-9]+).*/\1/' | sort -u) || true
if [ -n "${PIDS:-}" ]; then
  for pid in ${PIDS}; do kill "${pid}" 2>/dev/null || true; done
  sleep 0.2
fi

# 5) Redis-locks van alleen deze market weg
sudo -u trader bash -lc '/srv/trading/.venv/bin/python - <<PY
from redis import Redis
r=Redis.from_url("redis://127.0.0.1:6379/0",decode_responses=True)
m="'${MARKET}'"
cand=set([f"lock:guard:{m}"])
for pat in (f"*{m}*lock*", f"guard:*:{m}*lock*"):
    cand.update(r.keys(pat))
for k in cand:
    try: r.delete(k)
    except: pass
print("DONE")
PY' >/dev/null 2>&1 || true

# 6) Starten + wachten op HTTP 200 (max 15s)
systemctl start "trading-guard@${MARKET}.service" >/dev/null 2>&1 || true
for i in {1..30}; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/metrics" || true)
  [ "${code}" = "200" ] && { log "OK ${MARKET}: :${PORT} -> 200"; break; }
  sleep 0.5
done
# Als geen 200, log maar val niet de shell uit
if [ "${code:-000}" != "200" ]; then
  echo "[fix-one] ${MARKET} geen 200 (laatste code ${code}). Zie journalctl."
fi

# 7) Reconciler weer aan
systemctl start trading-guard-reconciler.service >/dev/null 2>&1 || true
