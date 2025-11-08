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

def day_dir() -> pathlib.Path:
    d = dt.datetime.utcnow().strftime("%Y-%m-%d")
    p = pathlib.Path(CONF["PARQUET_DIR"]) / d / "trades"
    p.mkdir(parents=True, exist_ok=True)
    return p

def write_jsonl(market: str, rows: list):
    fn = day_dir() / f"{market.replace('/', '-')}.jsonl"
    with open(fn, "ab") as f:
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

# Markets bepalen (ALL => alle -EUR)
def get_markets():
    if CONF["INGEST_MARKETS"].upper() == "ALL":
        mkts = [m["market"] for m in bv.markets({})]
        return [m for m in mkts if m.endswith("-EUR")]
    return [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]

markets = get_markets()
print(f"[trades] subscribing {len(markets)} markets..", file=sys.stderr)

batch = {}

def on_event(ev):
    # RAW-first: velden exact zoals Bitvavo ze levert
    if not isinstance(ev, dict):
        return
    # trades WS levert objecten met o.a. market, id, amount, price, side, timestamp
    # hameren op consistentie van market-key varianten
    m = ev.get("market") or ev.get("marketId") or ev.get("pair") or "unknown"
    # Redis streamnaam volgt het 'event'-veld indien aanwezig, anders 'trades'
    event_name = ev.get("event") or "trades"
    r.xadd(f"bitvavo:{event_name}", {"data": jsonf.dumps(ev)})
    batch.setdefault(m, []).append(ev)
    if len(batch[m]) >= 100:   # trades zijn high-freq → kleinere batch
        write_jsonl(m, batch[m]); batch[m] = []

def on_error(code, msg):
    print(f"[error] {code} {msg}", file=sys.stderr)

def on_open():
    subs = [{"name": "trades", "markets": markets}]
    bv.subscription("subscribe", subs, on_event)

bv.websocket(on_open, on_event, on_error)

# Flush bij stop
for m, rows in list(batch.items()):
    if rows:
        write_jsonl(m, rows)
print("[trades] stopped", file=sys.stderr)
