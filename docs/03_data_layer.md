# Hoofdstuk 3 — Datalaag (Redis & Opslag)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Start eerst Redis via Compose.
- Valideer connectiviteit met redis-cli.
- Sluit elke stap af met snapshot MD.

---

## 3.1 Redis deploy
```bash
sudo -u trader bash -lc 'cat > /srv/trading/compose/docker-compose.data.yml <<YML
name: datastack
services:
  redis:
    image: redis:7-alpine
    command: ["redis-server","--appendonly","yes"]
    restart: unless-stopped
    ports: ["6379:6379"]
    volumes: ["/srv/trading/storage/redis:/data"]
YML'

docker compose -f /srv/trading/compose/docker-compose.data.yml up -d
```
**Validatie:** `docker ps | grep redis` & `docker run --rm --network host redis:7-alpine redis-cli ping` → `PONG`.

### Stap-afsluiting
```bash
cat > ~/STEP-3.1-redis.md <<'MD'
# STEP 3.1 — Redis
- container: up
- ping: PONG
MD
```

---

## 3.2 Opslagpaden
- Parquet (JSONL eerst): `/srv/trading/storage/parquet/<YYYY-MM-DD>/`
- Logs: `/srv/trading/logs`

### Stap-afsluiting
```bash
cat > ~/STEP-3.2-storage.md <<'MD'
# STEP 3.2 — Storage
- parquet dir exists
- logs dir exists
MD
```
