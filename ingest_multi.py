import os, sys, signal, pathlib, datetime as dt
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

r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def day_dir(category: str, market: str) -> pathlib.Path:
    d = dt.datetime.utcnow().strftime("%Y-%m-%d")
    base = pathlib.Path(CONF["PARQUET_DIR"]) / d
    if category == "trades":
        base = base / "trades"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{market.replace('/', '-')}.jsonl"

def append_jsonl(path: pathlib.Path, rows: list):
    with open(path, "ab") as f:
        for row in rows:
            f.write(jsonf.dumps(row) + b"\n")

running = True
def stop(*_):
    global running; running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

bv = Bitvavo({
    "APIKEY": CONF["BITVAVO_API_KEY"],
    "APISECRET": CONF["BITVAVO_API_SECRET"],
    "RESTURL": "https://api.bitvavo.com/v2",
    "WSURL": "wss://ws.bitvavo.com/v2",
    "ACCESSWINDOW": 10000,
})

def get_markets():
    if CONF["INGEST_MARKETS"].upper() == "ALL":
        mkts = [m["market"] for m in bv.markets({})]
        return [m for m in mkts if m.endswith("-EUR")]
    return [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]

markets = get_markets()
print(f"[multi] subscribing {len(markets)} markets to ticker24h + trades..", file=sys.stderr)

# Batches per (category, market)
batch = {}  # key = (category, market) -> list
BATCH_LIMIT = {"ticker24h": 500, "trades": 100}

def handle_event(category: str, ev: dict):
    if not isinstance(ev, dict):
        return
    m = ev.get("market") or ev.get("marketId") or ev.get("pair") or "unknown"
    # Redis stream (RAW-first)
    r.xadd(f"bitvavo:{category}", {"data": jsonf.dumps(ev)})
    # File batch
    key = (category, m)
    batch.setdefault(key, []).append(ev)
    if len(batch[key]) >= BATCH_LIMIT.get(category, 500):
        append_jsonl(day_dir(category, m), batch[key]); batch[key] = []

def on_event(ev):
    # Universele callback voor ws: expect dicts met "event"
    if not isinstance(ev, dict): 
        return
    evt = ev.get("event")
    if evt == "ticker24h":
        handle_event("ticker24h", ev)
    elif evt == "trades":
        handle_event("trades", ev)
    else:
        # andere events negeren, maar wel raw naar een aparte stream
        r.xadd("bitvavo:other", {"data": jsonf.dumps(ev)})

def on_error(code, msg):
    print(f"[error] {code} {msg}", file=sys.stderr)

def on_open():
    subs = [
        {"name": "ticker24h", "markets": markets},
        {"name": "trades",    "markets": markets},
    ]
    bv.subscription("subscribe", subs, on_event)

# Gebruik exact dezelfde WS-starter als in je werkende ingest.py
bv.websocket(on_open, on_event, on_error)

# Flush restbatches bij stop
for (category, m), rows in list(batch.items()):
    if rows:
        append_jsonl(day_dir(category, m), rows)
print("[multi] stopped", file=sys.stderr)
