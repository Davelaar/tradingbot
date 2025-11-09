# Hoofdstuk 4 — Python omgeving & Ingest (Bitvavo SDK)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Installeer Python 3.12 + venv, installeer packages.
- Maak `.env.bitvavo`, schrijf `ingest.py`, run en valideer.
- Na elke stap: snapshot MD.

---

## 4.1 Python 3.12 + venv + packages
```bash
apt-get install -y python3.12 python3.12-venv python3-pip
sudo -u trader bash -lc '
python3.12 -m venv /srv/trading/.venv
source /srv/trading/.venv/bin/activate
pip install --upgrade pip
pip install python-bitvavo-api==1.4.2 redis==5.0.7 pyarrow==17.0.0 orjson==3.10.*
'
```
**Validatie:** `python -c "import bitvavo"` werkt in de venv.

### Stap-afsluiting
```bash
cat > ~/STEP-4.1-python.md <<'MD'
# STEP 4.1 — Python
- venv: /srv/trading/.venv
- packages: python-bitvavo-api, redis, pyarrow, orjson
MD
```

---

## 4.2 `.env.bitvavo`
```bash
cat > /srv/trading/.env.bitvavo <<'ENV'
BITVAVO_API_KEY=__your_key__
BITVAVO_API_SECRET=__your_secret__
INGEST_MARKETS=ALL
REDIS_URL=redis://127.0.0.1:6379/0
PARQUET_DIR=/srv/trading/storage/parquet
ENV
chown trader:trader /srv/trading/.env.bitvavo
chmod 600 /srv/trading/.env.bitvavo
```
**Validatie:** `grep -E "KEY|SECRET" -n /srv/trading/.env.bitvavo` toont waarden (niet delen).

### Stap-afsluiting
```bash
cat > ~/STEP-4.2-env.md <<'MD'
# STEP 4.2 — .env.bitvavo
- keys present: yes
- markets: ALL
- redis_url: redis://127.0.0.1:6379/0
- parquet_dir: /srv/trading/storage/parquet
MD
```

---

## 4.3 `ingest.py` (WS → Redis Streams → JSONL/Parquet)
```bash
cat > /srv/trading/ingest.py <<'PY'
import os, sys, time, signal, pathlib, datetime as dt
import orjson as jsonf
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

CONF = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "INGEST_MARKETS": os.getenv("INGEST_MARKETS", "ALL"),
}

r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def today_dir():
    d = dt.datetime.utcnow().strftime("%Y-%m-%d")
    p = pathlib.Path(CONF["PARQUET_DIR"]) / d
    p.mkdir(parents=True, exist_ok=True); return p

def write_jsonl(market, rows):
    fn = today_dir() / f"{market.replace('/', '-')}.jsonl"
    with open(fn, "ab") as f:
        for row in rows:
            f.write(jsonf.dumps(row) + b"\n")

running = True
def stop(*a): 
    global running; running = False

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

bv = Bitvavo({"RESTURL":"https://api.bitvavo.com/v2","WSURL":"wss://ws.bitvavo.com/v2","ACCESSWINDOW":10000})

key = os.getenv("BITVAVO_API_KEY"); sec = os.getenv("BITVAVO_API_SECRET")
if key and sec:
    bv.setAPIKey(key, sec)

def get_markets():
    if CONF["INGEST_MARKETS"].upper() == "ALL":
        mkts = [m["market"] for m in bv.markets({})]
        return [m for m in mkts if m.endswith("-EUR")]
    return [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]

markets = get_markets()
print(f"[ingest] subscribing {len(markets)} markets..", file=sys.stderr)
batch = {}

def on_event(ev):
    if "event" not in ev: return
    m = ev.get("market") or ev.get("marketId") or ev.get("pair") or "unknown"
    batch.setdefault(m, []).append(ev)
    r.xadd(f"bitvavo:{ev['event']}", {"data": jsonf.dumps(ev)})
    if len(batch[m]) >= 500:
        write_jsonl(m, batch[m]); batch[m] = []

def on_error(code, msg):
    print(f"[error] {code} {msg}", file=sys.stderr)

def on_open():
    subs = [{"name": "ticker24h", "markets": markets}]
    bv.subscription("subscribe", subs, on_event)

bv.websocket(on_open, on_event, on_error)

for m, rows in list(batch.items()):
    if rows: write_jsonl(m, rows)
print("[ingest] stopped", file=sys.stderr)
PY
```

### 4.4 Run & validatie
```bash
sudo -u trader bash -lc '
source /srv/trading/.venv/bin/activate
export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
python /srv/trading/ingest.py
'
# In andere shell:
docker run --rm --network host redis:7-alpine redis-cli xlen bitvavo:ticker24h
```
**Validatie:** Redis stream groeit; JSONL-bestanden verschijnen onder `/srv/trading/storage/parquet/<date>/`.

### Stap-afsluiting
```bash
cat > ~/STEP-4.4-ingest-run.md <<'MD'
# STEP 4.4 — Ingest run
- markets subscribed: <n>
- redis stream xlen: <value>
- files written: <paths>
MD
```
