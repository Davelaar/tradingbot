import os, sys, time, signal, pathlib, datetime as dt
import orjson as jsonf
from collections import deque
from typing import Dict, List, Optional, Tuple
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

from tradingbot_storage.parquet_sink import ParquetConfig, ParquetSink

CONF = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "INGEST_MARKETS": os.getenv("INGEST_MARKETS", "ALL"),
  "ORDERBOOK_DEPTH": int(os.getenv("ORDERBOOK_DEPTH", "100")),
  "SUB_CHUNK": int(os.getenv("SUB_CHUNK", "25")),
  "SLEEP_BETWEEN_SUBS": float(os.getenv("SLEEP_BETWEEN_SUBS", "0.05")),
  "SLEEP_BETWEEN_CHUNKS": float(os.getenv("SLEEP_BETWEEN_CHUNKS", "1.0")),
  "RATE_MIN": int(os.getenv("RATE_MIN", "200")),
  "BITVAVO_API_KEY": os.getenv("BITVAVO_API_KEY", ""),
  "BITVAVO_API_SECRET": os.getenv("BITVAVO_API_SECRET", ""),
  "HTTP_TIMEOUT": float(os.getenv("HTTP_TIMEOUT", "10.0")),
  # Niet-blokkerende grace: hoe lang we MAX parallel willen wachten dat N+1 binnenloopt
  "DRAIN_GRACE_MS": int(os.getenv("DRAIN_GRACE_MS", "250")),
}

# IO helpers
r = Redis.from_url(CONF["REDIS_URL"], decode_responses=False)

def day_dir(kind: str) -> pathlib.Path:
  d = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
  base = pathlib.Path(CONF["PARQUET_DIR"]).expanduser()
  p = base / d / "orderbook" / kind
  p.mkdir(parents=True, exist_ok=True)
  return p

def append_jsonl(kind: str, market: str, payload: dict):
  fn = day_dir(kind) / f"{market.replace('/', '-')}.jsonl"
  with open(fn, "ab") as f:
    f.write(jsonf.dumps(payload) + b"\n")


PARQUET_SINK = ParquetSink(ParquetConfig.from_env())
_PARQUET_BATCH_LIMIT = {"snapshot": 1, "update": 200, "top": 400}
_PARQUET_BUFFER = {}
_PARQUET_LAST_FLUSH = time.time()
_PARQUET_FLUSH_SECS = 5


def _parquet_key(kind: str, market: str):
  return (kind, market)


def parquet_append(kind: str, market: str, payload: dict):
  key = _parquet_key(kind, market)
  bucket = _PARQUET_BUFFER.setdefault(key, [])
  bucket.append(payload)
  if len(bucket) >= _PARQUET_BATCH_LIMIT[kind]:
    parquet_flush(kind, market)


def parquet_flush(kind: Optional[str] = None, market: Optional[str] = None):
  global _PARQUET_LAST_FLUSH
  targets = []
  if kind is not None and market is not None:
    targets.append(_parquet_key(kind, market))
  else:
    targets.extend(list(_PARQUET_BUFFER.keys()))

  for key in targets:
    rows = _PARQUET_BUFFER.get(key)
    if not rows:
      continue
    event, mkt = key
    PARQUET_SINK.write(f"orderbook:{event}", mkt, rows)
    _PARQUET_BUFFER[key] = []
    _PARQUET_LAST_FLUSH = time.time()


def parquet_flush_if_due():
  global _PARQUET_LAST_FLUSH
  if time.time() - _PARQUET_LAST_FLUSH >= _PARQUET_FLUSH_SECS:
    parquet_flush()

def xadd(market: str, obj: dict):
  r.xadd(f"bitvavo:book:{market}", {"data": jsonf.dumps(obj)})

def xadd_top(obj: dict):
  r.xadd("bitvavo:book", {"data": jsonf.dumps(obj)})


def emit_top_of_book(market: str, lb: 'LocalBook', origin: str):
  top = lb.current_top()
  if not top:
    return
  if lb.last_top == top:
    return
  bid_price, bid_amount, ask_price, ask_amount = top
  payload = {
    "event": "topOfBook",
    "market": market,
    "bestBid": bid_price,
    "bestBidSize": bid_amount,
    "bestAsk": ask_price,
    "bestAskSize": ask_amount,
    "nonce": lb.last_nonce,
    "source": origin,
    "timestamp": int(time.time()*1000),
  }
  xadd_top(payload)
  append_jsonl("top", market, payload)
  parquet_append("top", market, payload)
  lb.last_top = top

def wait_for_budget(bv: Bitvavo):
  while True:
    try:
      rem = bv.getRemainingLimit()
    except Exception:
      rem = 0
    if rem is None:
      time.sleep(0.3); continue
    if rem >= CONF["RATE_MIN"]:
      return
    time.sleep(0.3)

# Lokale state per markt
class LocalBook:
  def __init__(self, depth: int):
    self.depth = depth
    self.bids: Dict[str, str] = {}
    self.asks: Dict[str, str] = {}
    self.last_nonce: int = -1
    self.seeded: bool = False
    self.buffer: deque = deque()         # ruwe updates (dicts)
    self.await_until: Optional[float] = None  # epoch-seconden (deadline) na snapshot
    self.last_top: Optional[Tuple[float, float, float, float]] = None

  def _apply_side(self, side: str, levels):
    book = self.bids if side == "bids" else self.asks
    for price, amount in levels:
      if amount in ("0","0.0","0.00000000"):
        book.pop(price, None)
      else:
        book[price] = amount
    # top N bijhouden
    if side == "bids":
      top = sorted(book.items(), key=lambda kv: float(kv[0]), reverse=True)[:self.depth]
      self.bids = dict(top)
    else:
      top = sorted(book.items(), key=lambda kv: float(kv[0]))[:self.depth]
      self.asks = dict(top)

  def apply_snapshot(self, snap: dict):
    self.bids, self.asks = {}, {}
    self._apply_side("bids", snap.get("bids", []))
    self._apply_side("asks", snap.get("asks", []))
    self.last_nonce = int(snap.get("nonce", -1))
    self.seeded = True
    self.last_top = None
    # Zet non-blocking grace-deadline
    self.await_until = time.time() + (CONF["DRAIN_GRACE_MS"] / 1000.0)

  def try_apply_update(self, upd: dict) -> bool:
    if not self.seeded:
      self.buffer.append(upd); return False
    n = int(upd.get("nonce", -1))
    if n == self.last_nonce + 1:
      self._apply_side("bids", upd.get("bids", []))
      self._apply_side("asks", upd.get("asks", []))
      self.last_nonce = n
      return True
    # Niet direct weggooien; in buffer houden (kan nog *de* missing N+1 zijn)
    self.buffer.append(upd)
    return False

  def can_drain_now(self) -> bool:
    """Mag in main-loop proberen door te trekken? (alleen na snapshot en binnen de grace)"""
    return self.seeded and self.await_until is not None and time.time() <= self.await_until

  def drain_step(self) -> bool:
    """
    Eén **niet-blokkerende** drain-stap:
    - Als expected (last_nonce+1) in buffer zit -> toepassen (één stap)
    - Anders: niets doen (wachten in volgende main-loop iteratie)
    Return:
      True  -> er is toegepast (we zijn verder in de keten)
      False -> geen stap mogelijk (nog wachten of grace is straks op)
    """
    if not self.seeded:
      return False
    expected = self.last_nonce + 1
    # pak laatste update per nonce (laatste wint)
    by_nonce: Dict[int, dict] = {}
    for u in list(self.buffer):
      try:
        n = int(u.get("nonce", -1))
        if n >= expected:
          by_nonce[n] = u
      except Exception:
        pass
    u = by_nonce.get(expected)
    if not u:
      return False
    # pas precies expected toe
    if self.try_apply_update(u):
      # buffer opschonen tot en met last_nonce
      self.buffer = deque([x for x in self.buffer if int(x.get("nonce", -1)) > self.last_nonce])
      return True
    return False

  def grace_expired(self) -> bool:
    return self.await_until is not None and time.time() > self.await_until

  def mark_out_of_sync(self):
    self.seeded = False
    self.await_until = None
    self.buffer.clear()
    self.last_top = None

  def current_top(self) -> Optional[Tuple[float, float, float, float]]:
    if not self.bids or not self.asks:
      return None
    try:
      bid_price, bid_amount = next(iter(self.bids.items()))
      ask_price, ask_amount = next(iter(self.asks.items()))
    except StopIteration:
      return None
    try:
      return (float(bid_price), float(bid_amount), float(ask_price), float(ask_amount))
    except (TypeError, ValueError):
      return None

class OrderbookIngest:
  def __init__(self):
    creds = {}
    if CONF["BITVAVO_API_KEY"] and CONF["BITVAVO_API_SECRET"]:
      creds = {'APIKEY': CONF["BITVAVO_API_KEY"], 'APISECRET': CONF["BITVAVO_API_SECRET"]}
    self.bv = Bitvavo({**creds, 'timeout': CONF["HTTP_TIMEOUT"]})
    self.ws = self.bv.newWebsocket()
    self.ws.setErrorCallback(lambda err: print(f"[ws-error] {err}", file=sys.stderr))
    self.depth = CONF["ORDERBOOK_DEPTH"]
    self.books: Dict[str, LocalBook] = {}

  def all_markets(self) -> List[str]:
    return [m["market"] for m in self.bv.markets({}) if m["market"].endswith("-EUR")]

  def pick_markets(self) -> List[str]:
    if CONF["INGEST_MARKETS"].upper() == "ALL":
      return self.all_markets()
    return [m.strip() for m in CONF["INGEST_MARKETS"].split(",") if m.strip()]

  def seed_snapshot(self, market: str) -> bool:
    try:
      wait_for_budget(self.bv)
      snap = self.bv.book(market, {"depth": self.depth})
    except Exception as e:
      print(f"[err] snapshot {market}: {e}", file=sys.stderr)
      return False
    lb = self.books.setdefault(market, LocalBook(self.depth))
    lb.apply_snapshot(snap)
    payload = {"event":"snapshot","market":market,"data":snap,"timestamp":int(time.time()*1000)}
    xadd(market, payload); append_jsonl("snapshot", market, payload)
    parquet_append("snapshot", market, payload)
    emit_top_of_book(market, lb, "snapshot")
    print(f"[seed] {market} bids={len(snap.get('bids',[]))} asks={len(snap.get('asks',[]))} nonce={lb.last_nonce}")
    return True

  def on_book_update(self, payload: dict, market: str):
    if not isinstance(payload, dict):
      return
    data = payload.get("data") if "data" in payload else payload
    if not isinstance(data, dict):
      return
    update = {
      "market": data.get("market", market),
      "nonce": data.get("nonce"),
      "bids": data.get("bids", []),
      "asks": data.get("asks", []),
    }
    obj = {"event":"bookUpdate","market":market,"data":update,"timestamp":int(time.time()*1000)}
    xadd(market, obj); append_jsonl("update", market, obj)
    parquet_append("update", market, obj)

    lb = self.books.setdefault(market, LocalBook(self.depth))
    # probeer toe te passen of bufferen
    applied = lb.try_apply_update(update)
    if applied:
      emit_top_of_book(market, lb, "realtime")
    if not applied and not lb.seeded:
      # nog vóór snapshot: gewoon bufferen
      return
    # als we al seeded zijn en er zit een gat, laten we de main-loop het oplossen
    # (of grace uitlopen → resnapshot)

  def run(self):
    markets = self.pick_markets()
    print(f"[orderbook] subscribing {len(markets)} markets (incremental) depth={self.depth} chunks={CONF['SUB_CHUNK']}")
    for i in range(0, len(markets), CONF["SUB_CHUNK"]):
      chunk = markets[i:i+CONF["SUB_CHUNK"]]
      # 1) subscribes
      for m in chunk:
        self.ws.subscriptionBookUpdate(m, lambda p, _m=m: self.on_book_update(p, _m))
        time.sleep(CONF["SLEEP_BETWEEN_SUBS"])
      # 2) snapshots (updates kunnen al bufferen)
      for m in chunk:
        self.seed_snapshot(m)
        time.sleep(0.005)
      time.sleep(CONF["SLEEP_BETWEEN_CHUNKS"])

    running = True
    def stop(*_): 
      nonlocal running; running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
      while running:
        # 1) parallel niet-blokkerend doorscrollen waar mogelijk
        progressed = 0
        for m, lb in list(self.books.items()):
          if not lb.seeded:
            # probeer opnieuw snapshotten (niet elke iteratie: throttle via budget)
            self.seed_snapshot(m)
            continue
          # seeded: als we binnen grace zitten, probeer 1 stap expected te zetten
          if lb.can_drain_now():
            if lb.drain_step():
              emit_top_of_book(m, lb, "buffered")
              progressed += 1
              continue
            # als niets te doen en grace is verlopen → resnapshot
            if lb.grace_expired():
              print(f"[resync] {m} grace expired at nonce={lb.last_nonce}", file=sys.stderr)
              lb.mark_out_of_sync()
          else:
            # buiten grace: niets te doen; we vertrouwen op de realtime updates
            pass

        # 2) klein slaapje om CPU te sparen; als we voortgang hadden, houden we het tempo hoog
        time.sleep(0.02 if progressed else 0.08)
        parquet_flush_if_due()

    finally:
      try: self.ws.closeSocket()
      except Exception: pass
      parquet_flush()
      print("[orderbook] stopped", file=sys.stderr)

if __name__ == "__main__":
  OrderbookIngest().run()
