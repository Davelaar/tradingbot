#!/usr/bin/env bash
set -Eeuo pipefail
QUIET="${QUIET:-1}"

MARKETS="$(
  curl -s 127.0.0.1:9111/metrics \
   | sed -n 's/^guard_port_assignment{market="\([^"]\+\)"} [0-9][0-9]*.*/\1/p' \
   | sort -u
)"
[ -z "${MARKETS}" ] && { echo "[fix-all] geen markets uit reconciler"; exit 0; }

for M in ${MARKETS}; do
  QUIET="${QUIET}" /srv/trading/tools/guard_fix_one.sh "${M}" || true
done

# Eindsanity (niet-fataal, compact)
PORTS=$(curl -s 127.0.0.1:9111/metrics \
  | sed -n 's/^guard_port_assignment{market="[^"]\+"} \([0-9][0-9]*\).*/\1/p' \
  | sort -nu)
for p in ${PORTS}; do curl -s -o /dev/null -w ":%s %s\n" ":$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$p/metrics)"; done
