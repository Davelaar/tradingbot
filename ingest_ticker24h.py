import os, sys, time, signal, pathlib, datetime as dt
import orjson as jsonf
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

CONF = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "TICKER_MARKETS": os.getenv("TICKER_MARKETS", "BTC-EUR,ETH-EUR"),
  "BITVAVO_API_KEY": os.getenv("BITVAVO_API_KEY", ""),
  "BITVAVO_API_SECRET": os.getenv("BITVAVO_API_SECRET", ""),
}

r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def day_dir() -> pathlib.Path:
  d = dt.datetime.utcnow().strftime("%Y-%m-%d")
  p = pathlib.Path(CONF["PARQUET_DIR"]) / d
  p.mkdir(parents=True, exist_ok=True)
  return p

def append_jsonl(market: str, rows: list):
  fn = day_dir() / f"{market.replace('/', '-')}.jsonl"
  with open(fn, "ab") as f:
    for row in rows:
      f.write(jsonf.dumps(row) + b"\n")

bv = Bitvavo({'APIKEY': CONF["BITVAVO_API_KEY"], 'APISECRET': CONF["BITVAVO_API_SECRET"]})
ws = bv.newWebsocket()
ws.setErrorCallback(lambda err: print(f"[ws-error] {err}", file=sys.stderr))

markets = [m.strip() for m in CONF["TICKER_MARKETS"].split(",") if m.strip()]
print(f"[ticker24h] subscribing {len(markets)} markets: {', '.join(markets)}", file=sys.stderr)

BATCH_LIMIT = 200
batch = {}  # market -> list
last_flush = time.time()
FLUSH_SECS = 5

def handle(ev: dict):
  m = ev.get("market") or "unknown"
  # Redis stream: bitvavo:ticker24h
  r.xadd("bitvavo:ticker24h", {"data": jsonf.dumps(ev)})
  # File batch
  bucket = batch.setdefault(m, [])
  bucket.append(ev)
  if len(bucket) >= BATCH_LIMIT:
    append_jsonl(m, bucket); batch[m] = []

def flush_if_due():
  global last_flush
  if time.time() - last_flush >= FLUSH_SECS:
    for m, rows in list(batch.items()):
      if rows:
        append_jsonl(m, rows); batch[m] = []
    last_flush = time.time()

def on_ticker24h(payload):
  # Per-markt subscription → dict verwacht; steun list ook, just in case
  if isinstance(payload, dict):
    handle(payload)
  elif isinstance(payload, list):
    for ev in payload:
      if isinstance(ev, dict):
        handle(ev)

for m in markets:
  ws.subscriptionTicker24h(m, on_ticker24h)

running = True
def stop(*_): 
  global running; running = False
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

try:
  while running:
    time.sleep(0.25)
    flush_if_due()
finally:
  try: ws.closeSocket()
  except Exception: pass
  # afsluit-flush
  for m, rows in list(batch.items()):
    if rows:
      append_jsonl(m, rows)
  print("[ticker24h] stopped", file=sys.stderr)
