import os, sys, time, signal, pathlib, datetime as dt
import orjson as jsonf
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

CONF = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "INGEST_MARKETS": os.getenv("INGEST_MARKETS", "ALL"),
  "CANDLE_INTERVALS": os.getenv("CANDLE_INTERVALS", "1m"),
  "CANDLE_LIMIT": int(os.getenv("CANDLE_LIMIT", "50")),      # max markten
  "SUB_CHUNK": int(os.getenv("SUB_CHUNK", "25")),            # per chunk
  "RATE_MIN": int(os.getenv("RATE_MIN", "100")),             # drempel; wacht als < RATE_MIN
  "SLEEP_BETWEEN_SUBS": float(os.getenv("SLEEP_BETWEEN_SUBS", "0.05")),
  "SLEEP_BETWEEN_CHUNKS": float(os.getenv("SLEEP_BETWEEN_CHUNKS", "1.0")),
  "BITVAVO_API_KEY": os.getenv("BITVAVO_API_KEY", ""),
  "BITVAVO_API_SECRET": os.getenv("BITVAVO_API_SECRET", ""),
}

r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def day_dir(interval: str) -> pathlib.Path:
  d = dt.datetime.utcnow().strftime("%Y-%m-%d")
  p = pathlib.Path(CONF["PARQUET_DIR"]) / d / "candles" / interval
  p.mkdir(parents=True, exist_ok=True)
  return p

def append_jsonl(interval: str, market: str, rows: list):
  fn = day_dir(interval) / f"{market.replace('/', '-')}.jsonl"
  with open(fn, "ab") as f:
    for row in rows:
      f.write(jsonf.dumps(row) + b"\n")

bv = Bitvavo({'APIKEY': CONF["BITVAVO_API_KEY"], 'APISECRET': CONF["BITVAVO_API_SECRET"]})
ws = bv.newWebsocket()

def all_markets():
  mkts = [m["market"] for m in bv.markets({})]
  return [m for m in mkts if m.endswith("-EUR")]

def pick_markets():
  if CONF["INGEST_MARKETS"].upper() == "ALL":
    mkts = all_markets()
  else:
    mkts = [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]
  return mkts[:CONF["CANDLE_LIMIT"]]

markets = pick_markets()
intervals = [i.strip() for i in CONF["CANDLE_INTERVALS"].split(",") if i.strip()]
print(f"[candles-rl] subscribing {len(markets)} markets × {intervals} (chunks {CONF['SUB_CHUNK']})", file=sys.stderr)

BATCH_LIMIT = 200
batch = {}  # key=(interval, market) -> list
last_flush = time.time()
FLUSH_SECS = 5

def handle(interval: str, market: str, candles: list):
  if not candles: return
  for c in candles:
    obj = {"market": market, "interval": interval, "candle": c}
    r.xadd(f"bitvavo:candles:{interval}", {"data": jsonf.dumps(obj)})
  key = (interval, market)
  bucket = batch.setdefault(key, [])
  for c in candles:
    bucket.append({"market": market, "interval": interval, "candle": c})
  if len(bucket) >= BATCH_LIMIT:
    append_jsonl(interval, market, bucket); batch[key] = []

def flush_if_due():
  global last_flush
  if time.time() - last_flush >= FLUSH_SECS:
    for (itv, m), rows in list(batch.items()):
      if rows:
        append_jsonl(itv, m, rows); batch[(itv, m)] = []
    last_flush = time.time()

def on_error(err):
  print(f"[ws-error] {err}", file=sys.stderr)

def on_candle(payload, interval, market):
  # Ondersteun zowel {'event':'candle','data':{...}} als platte dict
  if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
    d = payload["data"]; candles = d.get("candle") or []
    handle(interval, d.get("market", market), candles if isinstance(candles, list) else [])
  elif isinstance(payload, dict):
    candles = payload.get("candle") or []
    handle(interval, payload.get("market", market), candles if isinstance(candles, list) else [])

ws.setErrorCallback(on_error)

def wait_for_budget():
  # wacht tot rate budget >= RATE_MIN
  while True:
    try:
      rem = bv.getRemainingLimit()
    except Exception as e:
      print(f"[rate] error getRemainingLimit: {e}", file=sys.stderr)
      rem = 0
    if rem is None:
      # defensief: als None terugkomt, even pauze
      time.sleep(0.5); continue
    if rem >= CONF["RATE_MIN"]:
      return rem
    time.sleep(0.5)

def chunked(seq, n):
  for i in range(0, len(seq), n):
    yield seq[i:i+n]

# Throttled subscribes per interval
for itv in intervals:
  for group in chunked(markets, CONF["SUB_CHUNK"]):
    wait_for_budget()
    for m in group:
      ws.subscriptionCandles(m, itv, lambda p, _itv=itv, _m=m: on_candle(p, _itv, _m))
      time.sleep(CONF["SLEEP_BETWEEN_SUBS"])
    time.sleep(CONF["SLEEP_BETWEEN_CHUNKS"])

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
  for (itv, m), rows in list(batch.items()):
    if rows:
      append_jsonl(itv, m, rows)
  print("[candles-rl] stopped", file=sys.stderr)
