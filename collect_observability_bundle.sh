#!/usr/bin/env bash
# Observability bundler (robuust, geen abort op kleine fouten)
set -Euo pipefail

OUT_DIR="/srv/trading"
STAMP="$(date +%Y%m%d_%H%M%S)"
WORK="${OUT_DIR}/_obs_audit_${STAMP}"
TARBALL="${OUT_DIR}/obs_audit_${STAMP}.tgz"

# Prometheus & Grafana toegang
PROM_URL="${PROM_URL:-http://127.0.0.1:9090}"

# Optie 1: basic auth
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
GRAFANA_USER="${GRAFANA_USER:-}"
GRAFANA_PASS="${GRAFANA_PASS:-}"
# Optie 2: API key (aanbevolen)
GRAFANA_API_KEY="${GRAFANA_API_KEY:-}"   # zet bv. "Bearer eyJrIjo..."

# ---------- helpers ----------
log(){ printf "==> %s\n" "$*"; }
copy_if_exists(){ [ -e "$1" ] && { mkdir -p "$(dirname "$2")"; cp -a "$1" "$2" 2>/dev/null || true; }; }
curl_s(){
  # stil, tolerant
  curl -fsS "$@" || true
}
curl_grafana(){
  local path="$1" out="$2"
  if [ -n "$GRAFANA_API_KEY" ]; then
    curl -fsS -H "Authorization: ${GRAFANA_API_KEY}" "${GRAFANA_URL}${path}" -o "$out" || true
  elif [ -n "$GRAFANA_USER" ] && [ -n "$GRAFANA_PASS" ]; then
    curl -fsS -u "${GRAFANA_USER}:${GRAFANA_PASS}" "${GRAFANA_URL}${path}" -o "$out" || true
  else
    echo '{"note":"grafana auth not provided"}' > "$out"
  fi
}
redact_file(){
  # Alleen verwerken als het bestand leesbaar is
  local src="$1" dst="$2"
  if [ ! -r "$src" ]; then
    echo "# skipped unreadable: $src" > "$dst"
    return 0
  fi
  awk '
    BEGIN{IGNORECASE=1}
    {
      line=$0
      if (line ~ /(SECRET|API(_|-)KEY|PASSWORD|TOKEN|PRIVATE|ACCESS|WEBHOOK).*=/) {
        sub(/=.*/, "=<redacted>", line)
      }
      print line
    }
  ' "$src" > "$dst" 2>/dev/null || echo "# redaction failed, raw omitted" > "$dst"
}

# ---------- start ----------
mkdir -p "$WORK"
log "Werkmap: $WORK"

# 1) Config-structuur
log "Verzamel statische config-bestanden"
copy_if_exists "/srv/trading/compose"               "$WORK/compose"
copy_if_exists "/srv/trading/web"                   "$WORK/web"
copy_if_exists "/srv/trading/tools"                 "$WORK/tools"
copy_if_exists "/srv/trading/metrics"               "$WORK/metrics"
copy_if_exists "/srv/trading/tradingbot/services"   "$WORK/tradingbot/services"

# 2) systemd units (best effort)
if [ -d /etc/systemd/system ]; then
  mkdir -p "$WORK/_systemd"
  find /etc/systemd/system -maxdepth 1 -type f \
    \( -name "*trading*.service" -o -name "*bitvavo*.service" -o -name "*grafana*.service" -o -name "*prometheus*.service" \) \
    -exec cp -a {} "$WORK/_systemd/" \; 2>/dev/null || true
fi

# 3) .env-bestanden met redactie (tolerant, skip onleesbaar)
log "Redigeer en kopieer .env-bestanden"
mkdir -p "$WORK/env"
# Note: sluit *.bak.* etc. uit door explicit te filteren
ENV_CANDIDATES=$(cat <<'EOF'
/srv/trading/.env
/srv/trading/.env.*
/srv/trading/tradingbot/.env
/srv/trading/tradingbot/.env.*
/srv/trading/secrets/*.env
EOF
)
shopt -s nullglob
for pattern in $ENV_CANDIDATES; do
  for f in $pattern; do
    base="$(basename "$f")"
    case "$base" in
      *.bak.*|*.backup|*.old) continue ;;  # problematische backups overslaan
    esac
    dst="$WORK/env/$base"
    redact_file "$f" "$dst"
  done
done
shopt -u nullglob

# 4) Docker status/netwerken (best effort)
log "Docker status en netwerken"
mkdir -p "$WORK/docker"
if command -v docker >/dev/null 2>&1; then
  docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$WORK/docker/ps.txt" 2>/dev/null || true
  docker network ls > "$WORK/docker/networks.txt" 2>/dev/null || true
  for net in obsnet metricsnet; do
    docker network inspect "$net" > "$WORK/docker/network_${net}.json" 2>/dev/null || echo '{}' > "$WORK/docker/network_${net}.json"
  done
  for y in /srv/trading/compose/docker-compose.*.yml; do
    [ -f "$y" ] || continue
    n="$(basename "$y")"
    {
      echo "\$ docker compose -f $y ps"
      docker compose -f "$y" ps 2>&1 || true
      echo
      echo "\$ docker compose -f $y config"
      docker compose -f "$y" config 2>&1 || true
    } > "$WORK/docker/compose__${n}.txt"
  done
  # observability logs (licht)
  if [ -f /srv/trading/compose/docker-compose.obs.yml ]; then
    docker compose -f /srv/trading/compose/docker-compose.obs.yml logs -n 200 > "$WORK/docker/logs_obs_tail.txt" 2>/dev/null || true
  fi
fi

# 5) Prometheus API dumps (tolerant)
log "Prometheus API-dumps"
mkdir -p "$WORK/prometheus"
curl_s "${PROM_URL}/api/v1/targets"           -o "$WORK/prometheus/targets.json"
curl_s "${PROM_URL}/api/v1/label/job/values"  -o "$WORK/prometheus/jobs.json"
curl_s "${PROM_URL}/api/v1/status/buildinfo"  -o "$WORK/prometheus/buildinfo.json"
curl_s "${PROM_URL}/api/v1/series?match[]=up" -o "$WORK/prometheus/series_up.json"

# 6) Grafana API dumps (tolerant, met API key of user/pass)
log "Grafana API-dumps"
mkdir -p "$WORK/grafana"
curl_grafana "/api/health"      "$WORK/grafana/health.json"
curl_grafana "/api/datasources" "$WORK/grafana/datasources.json"
curl_grafana "/api/search?query=&type=dash-db" "$WORK/grafana/search.json"
# Exporteer dashboards (als we een lijst hebben)
if command -v jq >/dev/null 2>&1; then
  UIDS="$(jq -r '.[].uid // empty' "$WORK/grafana/search.json" 2>/dev/null || true)"
  for uid in $UIDS; do
    curl_grafana "/api/dashboards/uid/${uid}" "$WORK/grafana/dashboard_${uid}.json"
  done
fi

# 7) Inventaris
log "Schrijf inventory"
{
  echo "# Observability Audit Inventory — ${STAMP}"
  echo
  echo "## Compose-bestanden:"
  [ -d "$WORK/compose" ] && find "$WORK/compose" -type f | sed 's#^#- #'
  echo
  echo "## Prometheus configuratie:"
  [ -f "$WORK/compose/prometheus.yml" ] && echo "- compose/prometheus.yml (aanwezig)" || echo "- compose/prometheus.yml (ONTBREEKT)"
  echo
  echo "## Weblaag configs:"
  [ -d "$WORK/web" ] && find "$WORK/web" -type f | sed 's#^#- #'
  echo
  echo "## Services (tradingbot):"
  [ -d "$WORK/tradingbot/services" ] && find "$WORK/tradingbot/services" -type f | sed 's#^#- #'
  echo
  echo "## Metrics scripts:"
  [ -d "$WORK/metrics" ] && find "$WORK/metrics" -type f | sed 's#^#- #'
  echo
  echo "## Systemd units:"
  [ -d "$WORK/_systemd" ] && find "$WORK/_systemd" -type f | sed 's#^#- #' || echo "- (geen kopieën gevonden)"
  echo
  echo "## .env (geredigeerd):"
  [ -d "$WORK/env" ] && find "$WORK/env" -type f | sed 's#^#- #' || echo "- (geen .env gevonden)"
} > "$WORK/INVENTORY.txt"

# 8) Maak tarball (altijd)
log "Maak archief: $TARBALL"
tar -C "$OUT_DIR" -czf "$TARBALL" "$(basename "$WORK")" 2>/dev/null || {
  # fallback (zonder compressie) – zou zelden nodig moeten zijn
  TARBALL="${OUT_DIR}/obs_audit_${STAMP}.tar"
  tar -C "$OUT_DIR" -cf "$TARBALL" "$(basename "$WORK")" 2>/dev/null || true
}
chmod 644 "$TARBALL" 2>/dev/null || true

log "Klaar. Bundle: $TARBALL"
