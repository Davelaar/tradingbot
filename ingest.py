import os, sys, time, signal, pathlib, datetime as dt
import orjson as jsonf
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

CONF = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "INGEST_MARKETS": os.getenv("INGEST_MARKETS", "ALL"),
  "BITVAVO_API_KEY": os.getenv("BITVAVO_API_KEY", ""),
  "BITVAVO_API_SECRET": os.getenv("BITVAVO_API_SECRET", ""),
}

# Redis
r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def day_dir(kind: str) -> pathlib.Path:
  d = dt.datetime.utcnow().strftime("%Y-%m-%d")
  base = pathlib.Path(CONF["PARQUET_DIR"]) / d
  if kind == "trades":
    base = base / "trades"
  base.mkdir(parents=True, exist_ok=True)
  return base

def append_jsonl(kind: str, market: str, rows: list):
  fn = day_dir(kind) / f"{market.replace('/', '-')}.jsonl"
  with open(fn, "ab") as f:
    for row in rows:
      f.write(jsonf.dumps(row) + b"\n")

# Bitvavo SDK
bv = Bitvavo({'APIKEY': CONF["BITVAVO_API_KEY"], 'APISECRET': CONF["BITVAVO_API_SECRET"]})
ws = bv.newWebsocket()
ws.setErrorCallback(lambda err: print(f"[ws-error] {err}", file=sys.stderr))

# Markten
def get_markets():
  if CONF["INGEST_MARKETS"].upper() == "ALL":
    mkts = [m["market"] for m in bv.markets({})]
    return [m for m in mkts if m.endswith("-EUR")]
  return [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]

markets = get_markets()
print(f"[ws] subscribing {len(markets)} markets to ticker24h + trades (per-market)..", file=sys.stderr)

# Batching + periodieke flush
BATCH_LIMIT = {"ticker24h": 500, "trades": 200}
batch = {}            # key=(evt, market) -> list
last_flush = time.time()
FLUSH_SECS = 10

def _market_of(ev: dict) -> str:
  return ev.get("market") or ev.get("marketId") or ev.get("pair") or "unknown"

def _handle(evt: str, ev: dict):
  # RAW → Redis
  r.xadd(f"bitvavo:{evt}", {"data": jsonf.dumps(ev)})
  # RAW → file (batch)
  m = _market_of(ev)
  key = (evt, m)
  bucket = batch.setdefault(key, [])
  bucket.append(ev)
  if len(bucket) >= BATCH_LIMIT[evt]:
    append_jsonl("trades" if evt == "trades" else "ticker24h", m, bucket)
    batch[key] = []

def _flush_if_due():
  global last_flush
  if time.time() - last_flush >= FLUSH_SECS:
    for (evt, m), rows in list(batch.items()):
      if rows:
        append_jsonl("trades" if evt == "trades" else "ticker24h", m, rows)
        batch[(evt, m)] = []
    last_flush = time.time()

# --- Callbacks (beide payloadvormen veilig ondersteunen) ---

def on_ticker24h(payload):
  # Docs laten {"event":"ticker24h","data":{...}} zien; SDK kan ook een platte dict geven
  if isinstance(payload, dict):
    if "data" in payload and isinstance(payload["data"], dict):
      ev = dict(payload["data"])       # neem het data-object
    else:
      ev = dict(payload)               # platte dict
    ev.setdefault("event", "ticker24h")
    _handle("ticker24h", ev)
  elif isinstance(payload, list):
    for item in payload:
      if isinstance(item, dict):
        ev = dict(item.get("data") if isinstance(item.get("data"), dict) else item)
        ev.setdefault("event", "ticker24h")
        _handle("ticker24h", ev)

def on_trades(payload):
  # Trades kunnen ook als {"event":"trades","data":{...}} of platte dict komen
  if isinstance(payload, dict):
    ev = dict(payload.get("data") if isinstance(payload.get("data"), dict) else payload)
    ev.setdefault("event", "trades")
    _handle("trades", ev)

# Per markt subscriben (string param)
for m in markets:
  ws.subscriptionTicker24h(m, on_ticker24h)
for m in markets:
  ws.subscriptionTrades(m, on_trades)

# Loop + nette afsluiting
running = True
def stop(*_): 
  global running; running = False
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

try:
  while running:
    time.sleep(0.25)
    _flush_if_due()
finally:
  try: ws.closeSocket()
  except Exception: pass
  # afsluit-flush
  for (evt, m), rows in list(batch.items()):
    if rows:
      append_jsonl("trades" if evt == "trades" else "ticker24h", m, rows)
  print("[ws] stopped", file=sys.stderr)
