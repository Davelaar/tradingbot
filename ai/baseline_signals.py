import os, time, json, math, collections, datetime as dt
from typing import Dict, Deque, Any, Tuple, List
from redis import Redis

CFG = {
  "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
  "SPREAD_BPS_MAX": float(os.getenv("SPREAD_BPS_MAX", "15")),
  "VOL_WINDOW": int(os.getenv("VOL_WINDOW", "30")),
  "VOL_STD_MIN": float(os.getenv("VOL_STD_MIN", "0.002")),
  "VOL_SPIKE_WINDOW": int(os.getenv("VOL_SPIKE_WINDOW", "60")),
  "VOL_SPIKE_MULT": float(os.getenv("VOL_SPIKE_MULT", "3.0")),
  "WICK_RATIO_MIN": float(os.getenv("WICK_RATIO_MIN", "2.0")),
  "SIGNAL_STREAM": os.getenv("SIGNAL_STREAM", "signals:baseline"),
  "IDLE_FLUSH_SEC": float(os.getenv("IDLE_FLUSH_SEC", "1")),
  "VERBOSE": os.getenv("VERBOSE", "0") in ("1","true","TRUE","yes","YES"),
}

r = Redis.from_url(CFG["REDIS_URL"], decode_responses=True)

STREAM_TICKER = "bitvavo:ticker24h"
STREAM_CANDLE = "bitvavo:candles:1m"
STREAM_BOOK   = "bitvavo:book"

class MktState:
    def __init__(self):
        self.returns: Deque[float] = collections.deque(maxlen=CFG["VOL_WINDOW"])
        self.volumes: Deque[float] = collections.deque(maxlen=CFG["VOL_SPIKE_WINDOW"])
        self.last_close: float = None
        self.last_bidask: Tuple[float,float] = (None, None)
        self.last_candle_ts: float = 0.0

state: Dict[str, MktState] = {}

def stddev(vals: List[float]) -> float:
    n = len(vals)
    if n < 2: return 0.0
    m = sum(vals)/n
    var = sum((v-m)*(v-m) for v in vals)/(n-1)
    return math.sqrt(var)

def wick_ratio(o: float, h: float, l: float, c: float) -> float:
    body = abs(c - o) or 1e-12
    upper = max(0.0, h - max(o, c))
    lower = max(0.0, min(o, c) - l)
    return max(upper/body, lower/body)

def parse_event(raw: str) -> Dict[str, Any]:
    try:
        ev = json.loads(raw)
        if isinstance(ev, str):
            ev = json.loads(ev)
        return ev
    except Exception:
        return {}

def _parse_candle_array(ev):
    c = ev.get("candle")
    if not isinstance(c, (list, tuple)) or len(c) < 6:
        return None
    try:
        o = float(c[1]); h = float(c[2]); l = float(c[3]); c_ = float(c[4]); v = float(c[5])
        return o, h, l, c_, v
    except Exception:
        return None

def _log(msg: str):
    if CFG["VERBOSE"]:
        print(msg, flush=True)

def eval_filters(mkt: str) -> Tuple[bool, Dict[str, Any], float, List[str]]:
    ms = state[mkt]
    reasons: List[str] = []
    score = 0.0
    details: Dict[str, Any] = {}

    b, a = ms.last_bidask
    spread_ok = False
    if b and a and a > 0:
        mid = 0.5*(a+b)
        spread_bps = (a - b)/mid * 1e4
        details["spread_bps"] = round(spread_bps, 4)
        spread_ok = spread_bps <= CFG["SPREAD_BPS_MAX"]
        if spread_ok:
            reasons.append(f"spread<={CFG['SPREAD_BPS_MAX']}bps")
            score += 1.0

    vol_ok = False
    if len(ms.returns) >= max(5, CFG["VOL_WINDOW"]//3):
        vol_std = stddev(list(ms.returns))
        details["vol_std"] = round(vol_std, 6)
        vol_ok = vol_std >= CFG["VOL_STD_MIN"]
        if vol_ok:
            reasons.append(f"vol_std>={CFG['VOL_STD_MIN']}")
            score += 1.0

    vol_spike = False
    if len(ms.volumes) >= 5:
        *hist, lastv = list(ms.volumes)
        meanv = (sum(hist)/len(hist)) if hist else 0.0
        details["vol_last"] = lastv
        details["vol_mean"] = round(meanv, 6)
        if meanv > 0 and lastv >= CFG["VOL_SPIKE_MULT"]*meanv:
            vol_spike = True
            reasons.append(f"volume>={CFG['VOL_SPIKE_MULT']}x")
            score += 1.0

    any_true = spread_ok or vol_ok or vol_spike or details.get("wick_ok", False)
    return any_true, details, score, reasons

def emit_signal(mkt: str, base: Dict[str, Any]):
    base["t"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    r.xadd(CFG["SIGNAL_STREAM"], base, maxlen=100000, approximate=True)

def _first_float(ev: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in ev and ev[key] is not None:
            try:
                return float(ev[key])
            except (TypeError, ValueError):
                continue
    return None


def handle_ticker(ev: Dict[str, Any]):
    mkt = ev.get("market") or ev.get("marketId") or ev.get("pair")
    if not mkt:
        return
    ms = state.setdefault(mkt, MktState())

    bid = _first_float(ev, "bestBid", "bid", "b")
    ask = _first_float(ev, "bestAsk", "ask", "a")
    if bid and ask and bid > 0 and ask > 0:
        ms.last_bidask = (bid, ask)

    last_price = _first_float(ev, "lastPrice", "price", "lastTradedPrice")
    if last_price and last_price > 0:
        if ms.last_close is not None and ms.last_close > 0:
            ret = (last_price - ms.last_close) / ms.last_close
            ms.returns.append(ret)
        ms.last_close = last_price

def handle_book(ev: Dict[str, Any]):
    mkt = ev.get("market") or ev.get("marketId") or ev.get("pair")
    if not mkt:
        return
    ms = state.setdefault(mkt, MktState())
    try:
        b = float(ev.get("bestBid") or ev.get("bid") or ev.get("b") or 0)
        a = float(ev.get("bestAsk") or ev.get("ask") or ev.get("a") or 0)
        if b > 0 and a > 0:
            ms.last_bidask = (b, a)
    except Exception:
        return

def handle_candle(ev: Dict[str, Any]):
    mkt = ev.get("market") or ev.get("marketId") or ev.get("pair")
    if not mkt:
        return
    parsed = None
    if all(k in ev for k in ("open","high","low","close")):
        try:
            o = float(ev.get("open")); h = float(ev.get("high")); l = float(ev.get("low")); c = float(ev.get("close"))
            v = float(ev.get("volume", 0))
            parsed = (o, h, l, c, v)
        except Exception:
            parsed = None
    if parsed is None and "candle" in ev:
        parsed = _parse_candle_array(ev)
    if parsed is None:
        return

    o, h, l, c, v = parsed
    ms = state.setdefault(mkt, MktState())

    if ms.last_close is not None and ms.last_close > 0:
        ret = (c - ms.last_close)/ms.last_close
        ms.returns.append(ret)
    ms.last_close = c

    ms.volumes.append(v)

    wr = wick_ratio(o, h, l, c)
    wick_ok = wr >= CFG["WICK_RATIO_MIN"]

    any_true, details, score, reasons = eval_filters(mkt)
    details.update({
      "market": mkt,
      "o": o, "h": h, "l": l, "c": c, "v": v,
      "wick_ratio": round(wr, 4),
      "wick_ok": wick_ok
    })
    if wick_ok:
        reasons.append(f"wick>={CFG['WICK_RATIO_MIN']}x")
        score += 1.0
        any_true = True

    if any_true:
        emit_signal(mkt, {
          "market": mkt,
          "score": round(score, 3),
          "reasons": json.dumps(reasons),
          "details": json.dumps(details),
        })
        _log(f"[signal] {mkt} score={round(score,3)} reasons={reasons}")

def pump():
    _log("[AI] baseline_signals started — waiting for Redis events...")
    ids = {STREAM_TICKER:"$", STREAM_CANDLE:"$"}
    has_book_agg = r.exists(STREAM_BOOK)
    if has_book_agg:
        ids[STREAM_BOOK] = "$"

    book_keys = []
    last_discover = 0.0
    if not has_book_agg:
        for k in r.scan_iter("bitvavo:book:*", count=1000):
            if k != STREAM_BOOK:
                book_keys.append(k)

    last_flush = time.time()
    while True:
        keys = [STREAM_CANDLE, STREAM_TICKER] + ([STREAM_BOOK] if has_book_agg else [])
        res = r.xread(streams=dict(zip(keys, [ids[k] for k in keys])), block=1000, count=500)
        for k, messages in res:
            for msg_id, fields in messages:
                ids[k] = msg_id
                raw = fields.get("data")
                if raw is None:
                    continue
                ev = parse_event(raw)
                if not ev:
                    continue
                if k == STREAM_CANDLE:
                    handle_candle(ev)
                elif k == STREAM_TICKER:
                    handle_ticker(ev)
                elif k == STREAM_BOOK:
                    handle_book(ev)

        now = time.time()
        if not has_book_agg and (now - last_flush) >= max(5, CFG["IDLE_FLUSH_SEC"]):
            if now - last_discover > 300:
                for k in r.scan_iter("bitvavo:book:*", count=1000):
                    if k != STREAM_BOOK and k not in book_keys:
                        book_keys.append(k)
                last_discover = now

            for bk in book_keys[:50]:
                if bk not in ids:
                    ids[bk] = "$"
                res2 = r.xread(streams={bk: ids[bk]}, block=1, count=100)
                for _k, messages in res2:
                    for msg_id, fields in messages:
                        ids[_k] = msg_id
                        ev = parse_event(fields.get("data"))
                        if ev:
                            handle_book(ev)
            last_flush = now

if __name__ == "__main__":
    pump()
