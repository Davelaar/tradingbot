# Hoofdstuk 8 — Lifecycle (Backups, Updates, Security)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Plan vaste routines; voer periodiek uit.
- Snapshot per onderdeel.

---

## 8.1 Backups
- Backup: `/srv/trading/storage/**` + `/srv/trading/filebrowser/**`.

### Stap-afsluiting
```bash
cat > ~/STEP-8.1-backups.md <<'MD'
# STEP 8.1 — Backups
- scope: storage + filebrowser db/config
- schedule: <cron>
MD
```

## 8.2 Updates
- `docker compose pull` en `up -d` per stack.

### Stap-afsluiting
```bash
cat > ~/STEP-8.2-updates.md <<'MD'
# STEP 8.2 — Updates
- stacks updated: web/data/obs/core
MD
```

## 8.3 Security
- ssh hardening; ufw; secrets chmod 600.

### Stap-afsluiting
```bash
cat > ~/STEP-8.3-security.md <<'MD'
# STEP 8.3 — Security
- ssh hardened
- ufw enabled
- secrets protected
MD
```
