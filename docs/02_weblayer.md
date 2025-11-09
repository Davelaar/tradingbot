# Hoofdstuk 2 — Weblaag met grafische filemanager
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Kies **1** van de routes hieronder: **Caddy + FileBrowser** (makkelijkste TLS) **of** Nginx + certbot.
- Gebruik **subdomein** (bv. `files.example.com`) en géén subpad.
- Aan het eind van **iedere stap** maak je een snapshot MD (zie *Stap-afsluiting* blokken).

---

## 2.1 DNS voorbereiden
- Zet A-record `files.<domein>` → server IPv4, optioneel AAAA → IPv6.
**Validatie:** `dig +short A files.<domein>` → jouw IPv4.

### Stap-afsluiting
```bash
cat > ~/STEP-2.1-dns.md <<'MD'
# STEP 2.1 — DNS
- files.<domein> A: <ip>
- AAAA: <ipv6 of none>
MD
```

---

## 2.2 Route A — Caddy + FileBrowser (aanbevolen)
```bash
sudo -u trader bash -lc 'cat > /srv/trading/compose/.env.web <<ENV
DOMAIN=files.example.com
LE_EMAIL=you@example.com
ENV'

sudo -u trader bash -lc 'cat > /srv/trading/web/Caddyfile <<CADDY
{
  email {$LE_EMAIL}
}
{$DOMAIN} {
  encode gzip
  reverse_proxy filebrowser:8080
}
CADDY'

sudo -u trader bash -lc 'cat > /srv/trading/compose/docker-compose.web.yml <<YML
name: webstack
services:
  filebrowser:
    image: filebrowser/filebrowser:latest
    command: -a 0.0.0.0 -p 8080 -r /srv/trading
    restart: unless-stopped
    networks: [web]
    volumes:
      - /srv/trading:/srv/trading
      - /srv/trading/filebrowser/db:/database
      - /srv/trading/filebrowser/config:/config
  caddy:
    image: caddy:2.10.2
    restart: unless-stopped
    networks: [web]
    ports: ["80:80","443:443"]
    environment:
      - DOMAIN=${DOMAIN}
      - LE_EMAIL=${LE_EMAIL}
    volumes:
      - /srv/trading/web/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
networks: { web: {} }
volumes: { caddy_data: {}, caddy_config: {} }
YML'

docker compose -f /srv/trading/compose/docker-compose.web.yml --env-file /srv/trading/compose/.env.web up -d

# Admin initialisatie (sterk wachtwoord, min 12 chars)
docker compose -f /srv/trading/compose/docker-compose.web.yml exec filebrowser sh -lc '
  filebrowser -c /config/settings.json config init >/dev/null 2>&1 || true
  filebrowser -c /config/settings.json config set --address 0.0.0.0 --port 8080 --root /srv/trading >/dev/null
  [ -f /database/filebrowser.db ] || filebrowser -c /config/settings.json -d /database/filebrowser.db users add admin "ChangeMe-Strong-123!" --perm.admin >/dev/null
'
```
**Validatie:** open `https://files.<domein>`; login `admin / ChangeMe-Strong-123!`; upload en **Extract/Zip** werkt.

### Stap-afsluiting
```bash
cat > ~/STEP-2.2-weblayer-caddy.md <<'MD'
# STEP 2.2 — Weblaag (Caddy+FileBrowser)
- site: https://files.<domein>
- login ok: yes/no
- upload: ok
- extract/zip: ok
MD
```

---

## 2.3 Route B — Nginx + certbot (alternatief)
```bash
apt-get install -y nginx python3-certbot-nginx
certbot --nginx -d files.<domein> -m you@example.com --agree-tos --redirect

# FileBrowser standalone op 127.0.0.1:8080
docker run -d --name filebrowser --restart unless-stopped -p 127.0.0.1:8080:8080 \
  -v /srv/trading:/srv/trading \
  -v /srv/trading/filebrowser/db:/database -v /srv/trading/filebrowser/config:/config \
  filebrowser/filebrowser:latest -a 0.0.0.0 -p 8080 -r /srv/trading

# Nginx server block proxy_pass → http://127.0.0.1:8080;
```
**Validatie:** `https://files.<domein>` werkt, upload/zip/unzip ok.

### Stap-afsluiting
```bash
cat > ~/STEP-2.3-weblayer-nginx.md <<'MD'
# STEP 2.3 — Weblaag (Nginx+certbot)
- site: https://files.<domein>
- upload: ok
- extract/zip: ok
MD
```
