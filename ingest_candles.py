import os, sys, time, signal, pathlib, datetime as dt
import orjson as jsonf
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

from tradingbot_storage.parquet_sink import ParquetConfig, ParquetSink

CONF = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "INGEST_MARKETS": os.getenv("INGEST_MARKETS", "ALL"),
  "CANDLE_INTERVALS": os.getenv("CANDLE_INTERVALS", "1m,5m,1h"),
  "BITVAVO_API_KEY": os.getenv("BITVAVO_API_KEY", ""),
  "BITVAVO_API_SECRET": os.getenv("BITVAVO_API_SECRET", ""),
}

r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def day_dir(interval: str) -> pathlib.Path:
  d = dt.datetime.utcnow().strftime("%Y-%m-%d")
  base = pathlib.Path(CONF["PARQUET_DIR"]).expanduser()
  p = base / d / "candles" / interval
  p.mkdir(parents=True, exist_ok=True)
  return p

def append_jsonl(interval: str, market: str, rows: list):
  fn = day_dir(interval) / f"{market.replace('/', '-')}.jsonl"
  with open(fn, "ab") as f:
    for row in rows:
      f.write(jsonf.dumps(row) + b"\n")


PARQUET_SINK = ParquetSink(ParquetConfig.from_env())


def flush_bucket(interval: str, market: str):
  key = (interval, market)
  rows = batch.get(key)
  if not rows:
    return

  append_jsonl(interval, market, rows)
  PARQUET_SINK.write(f"candles:{interval}", market, rows)
  batch[key] = []

bv = Bitvavo({'APIKEY': CONF["BITVAVO_API_KEY"], 'APISECRET': CONF["BITVAVO_API_SECRET"]})
ws = bv.newWebsocket()
ws.setErrorCallback(lambda err: print(f"[ws-error] {err}", file=sys.stderr))

def get_markets():
  if CONF["INGEST_MARKETS"].upper() == "ALL":
    mkts = [m["market"] for m in bv.markets({})]
    return [m for m in mkts if m.endswith("-EUR")]
  return [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]

markets = get_markets()
intervals = [i.strip() for i in CONF["CANDLE_INTERVALS"].split(",") if i.strip()]
print(f"[candles] subscribing {len(markets)} markets × {intervals}", file=sys.stderr)

# batching per (interval, market)
BATCH_LIMIT = 200
batch = {}  # key=(interval, market) -> list
last_flush = time.time()
FLUSH_SECS = 5

def handle(interval: str, market: str, candles: list):
  # RAW → Redis stream per interval
  for c in candles:
    obj = {"market": market, "interval": interval, "candle": c}
    r.xadd(f"bitvavo:candles:{interval}", {"data": jsonf.dumps(obj)})
  # RAW → file batch
  key = (interval, market)
  bucket = batch.setdefault(key, [])
  for c in candles:
    bucket.append({"market": market, "interval": interval, "candle": c})
  if len(bucket) >= BATCH_LIMIT:
    flush_bucket(interval, market)

def flush_if_due():
  global last_flush
  if time.time() - last_flush >= FLUSH_SECS:
    for (interval, market), rows in list(batch.items()):
      if rows:
        flush_bucket(interval, market)
    last_flush = time.time()

def on_candle(payload, interval, market):
  # Docs tonen {"event":"candle","market", "interval", "candle":[[...]]}
  # Sommige wrappers kunnen een envelop onder "data" geven; ondersteun beide.
  if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
    d = payload["data"]
    candles = d.get("candle") or []
    handle(interval, d.get("market", market), candles if isinstance(candles, list) else [])
  elif isinstance(payload, dict):
    candles = payload.get("candle") or []
    handle(interval, payload.get("market", market), candles if isinstance(candles, list) else [])
  # (candles komen normaal gesproken als lijst van lijsten; lege periodes geven niets)

# per markt × interval subscriben (SDK: subscriptionCandles)
for m in markets:
  for itv in intervals:
    ws.subscriptionCandles(m, itv, lambda p, _itv=itv, _m=m: on_candle(p, _itv, _m))

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
  for (interval, market), rows in list(batch.items()):
    if rows:
      flush_bucket(interval, market)
  print("[candles] stopped", file=sys.stderr)
