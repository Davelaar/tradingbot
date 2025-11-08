import os, sys, time, math, signal, pathlib, datetime as dt
from typing import List, Tuple, Dict, Iterable
import orjson as jsonf
from python_bitvavo_api.bitvavo import Bitvavo

CONF = {
  "PARQUET_DIR": os.getenv("PARQUET_DIR", "/srv/trading/storage/parquet"),
  "BACKFILL_INTERVALS": os.getenv("BACKFILL_INTERVALS", "1m,5m,1h"),
  "BACKFILL_MARKETS": os.getenv("BACKFILL_MARKETS", "ALL"),     # "ALL" of CSV
  # Kies óf uren terug óf expliciete datums (UTC ISO8601 zonder tz)
  "BACKFILL_HOURS": int(os.getenv("BACKFILL_HOURS", "24")),
  "BACKFILL_START": os.getenv("BACKFILL_START", ""),             # bv. "2025-10-28 00:00:00"
  "BACKFILL_END": os.getenv("BACKFILL_END", ""),                 # bv. "2025-10-29 00:00:00"
  # Rate-limit besturing
  "RATE_MIN": int(os.getenv("RATE_MIN", "200")),                 # wacht als remaining < RATE_MIN
  "SLEEP_BETWEEN_CALLS": float(os.getenv("SLEEP_BETWEEN_CALLS", "0.05")),
  # API keys zijn optioneel voor public endpoints; met key kun je vaak meer headroom krijgen
  "BITVAVO_API_KEY": os.getenv("BITVAVO_API_KEY", ""),
  "BITVAVO_API_SECRET": os.getenv("BITVAVO_API_SECRET", ""),
}

# Mapping van intervalstring → (millis per candle, minuten per request-chunk)
# Kies conservatief zodat responses niet te groot worden.
INTERVALS: Dict[str, Tuple[int, int]] = {
  "1m": (60_000, 360),     # 6 uur per request
  "5m": (5*60_000, 24*60), # 24 uur per request
  "1h": (60*60_000, 7*24*60),  # 7 dagen per request
}

def utc_ms(dt_obj: dt.datetime) -> int:
  return int(dt_obj.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def parse_boundaries() -> Tuple[int, int]:
  if CONF["BACKFILL_START"] and CONF["BACKFILL_END"]:
    start = dt.datetime.strptime(CONF["BACKFILL_START"], "%Y-%m-%d %H:%M:%S")
    end   = dt.datetime.strptime(CONF["BACKFILL_END"],   "%Y-%m-%d %H:%M:%S")
  else:
    end = dt.datetime.utcnow()
    start = end - dt.timedelta(hours=CONF["BACKFILL_HOURS"])
  s_ms, e_ms = utc_ms(start), utc_ms(end)
  if s_ms >= e_ms:
    raise ValueError("BACKFILL range ongeldig: start >= end")
  return s_ms, e_ms

def ensure_day_dir(interval: str, when_ms: int) -> pathlib.Path:
  day = dt.datetime.utcfromtimestamp(when_ms/1000.0).strftime("%Y-%m-%d")
  p = pathlib.Path(CONF["PARQUET_DIR"]) / day / "candles" / interval
  p.mkdir(parents=True, exist_ok=True)
  return p

def out_file(interval: str, market: str, when_ms: int) -> pathlib.Path:
  return ensure_day_dir(interval, when_ms) / f"{market.replace('/', '-')}.jsonl"

def chunk_ranges(interval: str, start_ms: int, end_ms: int) -> Iterable[Tuple[int,int]]:
  ms_per_candle, minutes_per_request = INTERVALS[interval]
  span_ms = minutes_per_request * 60_000
  s = start_ms
  while s < end_ms:
    e = min(end_ms, s + span_ms)
    yield (s, e)
    s = e

def pick_markets(bv: Bitvavo) -> List[str]:
  if CONF["BACKFILL_MARKETS"].upper() == "ALL":
    mkts = [m["market"] for m in bv.markets({})]
    return [m for m in mkts if m.endswith("-EUR")]
  return [m.strip() for m in CONF["BACKFILL_MARKETS"].split(",") if m.strip()]

def wait_for_budget(bv: Bitvavo):
  # REST-budgetmeter: wacht tot remaining >= RATE_MIN
  while True:
    try:
      rem = bv.getRemainingLimit()
    except Exception as e:
      # defensief: bij error even pauze en opnieuw proberen
      print(f"[rate] error getRemainingLimit: {e}", file=sys.stderr)
      rem = 0
    if rem is None:
      time.sleep(0.5); continue
    if rem >= CONF["RATE_MIN"]:
      return rem
    time.sleep(0.5)

def write_jsonl(path: pathlib.Path, rows: List[dict]):
  if not rows:
    return
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(path, "ab") as f:
    for r in rows:
      f.write(jsonf.dumps(r) + b"\n")

def backfill_market_interval(bv: Bitvavo, market: str, interval: str, s_ms: int, e_ms: int):
  ms_per_candle, _ = INTERVALS[interval]
  total = 0
  for (win_s, win_e) in chunk_ranges(interval, s_ms, e_ms):
    wait_for_budget(bv)
    # Call: REST/candles per Bitvavo SDK; params met 'interval','start','end'
    # Verwachte response: lijst van lijsten [tOpen, open, high, low, close, volume]
    try:
      items = bv.candles(market, {"interval": interval, "start": win_s, "end": win_e})
    except Exception as e:
      print(f"[err] candles {market} {interval} {win_s}-{win_e}: {e}", file=sys.stderr)
      # mini backoff
      time.sleep(1.0)
      continue

    # Normaliseer & schrijf per candle naar dagbestand van tOpen
    rows_by_day: Dict[str, List[dict]] = {}
    for c in (items or []):
      # Behoedzaam parsen; Bitvavo levert strings voor prijzen/volumes
      if not isinstance(c, (list, tuple)) or len(c) < 6: 
        continue
      t_open = int(c[0])
      obj = {
        "market": market,
        "interval": interval,
        "candle": c  # raw: [openTimeMs, open, high, low, close, volume]
      }
      day = dt.datetime.utcfromtimestamp(t_open/1000.0).strftime("%Y-%m-%d")
      rows_by_day.setdefault(day, []).append(obj)

    for day, rows in rows_by_day.items():
      path = pathlib.Path(CONF["PARQUET_DIR"]) / day / "candles" / interval / f"{market.replace('/', '-')}.jsonl"
      write_jsonl(path, rows)

    total += len(items or [])
    print(f"[ok] {market} {interval} {win_s}->{win_e} candles={len(items or [])} total={total}")
    time.sleep(CONF["SLEEP_BETWEEN_CALLS"])

def main():
  # Init Bitvavo
  key = CONF["BITVAVO_API_KEY"]; sec = CONF["BITVAVO_API_SECRET"]
  creds = {'APIKEY': key, 'APISECRET': sec} if key and sec else {}
  bv = Bitvavo(creds)

  # Input validatie
  intervals = [i.strip() for i in CONF["BACKFILL_INTERVALS"].split(",") if i.strip()]
  for itv in intervals:
    if itv not in INTERVALS:
      raise ValueError(f"Niet-ondersteund interval: {itv} (toegestaan: {list(INTERVALS)})")
  start_ms, end_ms = parse_boundaries()

  markets = pick_markets(bv)
  print(f"[start] markets={len(markets)} intervals={intervals} rangeUTC=({start_ms}..{end_ms})")

  # Ctrl-C vriendelijk
  running = True
  def stop(*_): 
    nonlocal running; running = False
  signal.signal(signal.SIGINT, stop)
  signal.signal(signal.SIGTERM, stop)

  try:
    for itv in intervals:
      if not running: break
      for m in markets:
        if not running: break
        backfill_market_interval(bv, m, itv, start_ms, end_ms)
  finally:
    print("[done] backfill finished")

if __name__ == "__main__":
  main()
