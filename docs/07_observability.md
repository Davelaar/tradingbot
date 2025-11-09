# Hoofdstuk 7 — Observability
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Optional: Prometheus + Grafana.
- Snapshot na elke stap.

---

## 7.1 Deploy
```bash
sudo -u trader bash -lc 'cat > /srv/trading/compose/docker-compose.obs.yml <<YML
name: observability
services:
  prometheus:
    image: prom/prometheus:latest
    restart: unless-stopped
    volumes:
      - /srv/trading/storage/prometheus:/prometheus
      - /srv/trading/compose/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports: ["9090:9090"]
  grafana:
    image: grafana/grafana:latest
    restart: unless-stopped
    volumes:
      - /srv/trading/storage/grafana:/var/lib/grafana
    ports: ["3000:3000"]
YML'
docker compose -f /srv/trading/compose/docker-compose.obs.yml up -d
```
**Validatie:** `:9090` en `:3000` luisteren (eventueel achter reverse proxy zetten).

### Stap-afsluiting
```bash
cat > ~/STEP-7.1-observability.md <<'MD'
# STEP 7.1 — Observability
- prometheus: up
- grafana: up
MD
```
