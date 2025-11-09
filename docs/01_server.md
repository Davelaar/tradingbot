# Hoofdstuk 1 — Serverfundament & Gebruikers (Ubuntu 24.04)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Werk stap-voor-stap, **kopieer exact** de blokken.
- Ga pas door naar de volgende stap als de huidige **gevalideerd** is.
- **Aan het eind van elke stap:** maak een korte snapshot-markdown (zie *Stap-afsluiting*). Die MD gebruik je als referentie in volgende hoofdstukken.

---

## 1.1 Nieuwe gebruiker + SSH-hardening
```bash
adduser --disabled-password --gecos "" trader
usermod -aG sudo trader

sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl restart ssh

# Pubkey plaatsen (PAS AAN naar jouw key)
install -d -m 700 -o trader -g trader ~trader/.ssh
echo "ssh-ed25519 AAAA... jouw-sleutel ..." >> ~trader/.ssh/authorized_keys
chown trader:trader ~trader/.ssh/authorized_keys
chmod 600 ~trader/.ssh/authorized_keys
```
**Validatie:** login als `trader` via SSH; root-login met wachtwoord is onmogelijk.

### Stap-afsluiting
Maak een snapshot MD op de server met je concrete uitkomsten:
```bash
cat > ~/STEP-1.1-ssh-user.md <<'MD'
# STEP 1.1 — SSH & gebruiker
- user: trader (sudo: yes)
- ssh: keys enabled, PasswordAuthentication=no, PermitRootLogin=prohibit-password
- pubkey fingerprint: <vul in>
MD
```

---

## 1.2 Basis packages + firewall
```bash
apt-get update -y
apt-get install -y curl git unzip zip htop jq ufw ca-certificates net-tools
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```
**Validatie:** `ufw status` toont 22/80/443 open.

### Stap-afsluiting
```bash
cat > ~/STEP-1.2-system-firewall.md <<'MD'
# STEP 1.2 — Packages & Firewall
- packages: curl git unzip zip htop jq ufw ca-certificates net-tools
- ufw: enabled, 22/80/443 allowed
MD
```

---

## 1.3 Docker Engine + Compose plugin
```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
bash -lc 'cat >/etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable
EOF'
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

usermod -aG docker trader
```
**Validatie:** `docker --version` en `docker compose version` werken; `groups trader` bevat `docker`.

### Stap-afsluiting
```bash
cat > ~/STEP-1.3-docker.md <<'MD'
# STEP 1.3 — Docker
- docker: $(docker --version)
- compose: $(docker compose version || echo "compose plugin ok")
- user trader in group docker: yes
MD
```

---

## 1.4 Directory-structuur
```bash
install -d -o trader -g trader /srv/trading/{compose,web,storage,logs}
install -d -o trader -g trader /srv/www/static
```
**Validatie:** `ls -ld /srv/trading /srv/www/static` → eigenaar `trader:trader`.

### Stap-afsluiting
```bash
cat > ~/STEP-1.4-dirs.md <<'MD'
# STEP 1.4 — Dirs
- created: /srv/trading/{compose,web,storage,logs}, /srv/www/static
- owner: trader:trader
MD
```
