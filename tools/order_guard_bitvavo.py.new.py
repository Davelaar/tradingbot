#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
order_guard_bitvavo.py — sluit LIVE posities via TP/SL/Trail
- Leest OPEN-intenties uit Redis (orders:live) ZONDER ze zelf te submitten
- Volgt prijs via eenvoudige REST-polling (tickerPrice) en plaatst de EXIT via Bitvavo REST
- Wijzigt niets aan jouw trading_core of submitter

Let op: Python SDK -> API-keys via constructor ('APIKEY'/'APISECRET'), niet setAPIKey().
"""
import os, time, json, math, signal, datetime as dt
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

def now_iso(): return dt.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

# === Config uit env ===
REDIS_URL      = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
ORDER_STREAM   = os.getenv("ORDER_OUTBOX_STREAM", "orders:live")
GROUP          = os.getenv("GUARD_GROUP", "trading_guard")
CONSUMER       = os.getenv("GUARD_CONSUMER", "guard-1")

RESTURL        = os.getenv("BITVAVO_REST_URL", "https://api.bitvavo.com/v2")
WSURL          = os.getenv("BITVAVO_WS_URL",   "wss://ws.bitvavo.com/v2")  # ongebruikt in deze versie
ACCESSWINDOW   = int(os.getenv("BITVAVO_ACCESSWINDOW","10000"))

API_KEY        = os.getenv("BITVAVO_API_KEY", "")
API_SECRET     = os.getenv("BITVAVO_API_SECRET", "")

# === Clients ===
r = Redis.from_url(REDIS_URL, decode_responses=True)

# Initialize Bitvavo client using options dict (SDK has no setAPIKey method)
bv = Bitvavo({
    "APIKEY": API_KEY,
    "APISECRET": API_SECRET,
    "RESTURL": os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2/"),
    "ACCESSWINDOW": 10000
}, "key")
running = True
def _stop(*_):
    global running
    running = False
signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

def log_event(level: str, msg: str, where: str, extra: Optional[Dict[str,Any]]=None):
    fields = {"ts": now_iso(), "lvl": level, "msg": msg, "where": where}
    if extra: fields.update(extra)
    try:
        r.xadd("trading:events", fields)
    except Exception:
        pass

def to_float(x, default=0.0):
    if x is None: return float(default)
    try:
        if isinstance(x,(float,int)): return float(x)
        return float(str(x))
    except: return float(default)

def base_amount(size_eur: float, price: float, base_decimals: int=8) -> float:
    if price <= 0: return 0.0
    amt = size_eur / price
    step = 10**(-base_decimals)
    amt = math.floor(amt/step)*step  # conservatief omlaag
    return float(amt)

def close_market(market: str, side_entry: str, price_ref: float, size_eur: float) -> Dict[str,Any]:
    side_close = "sell" if side_entry.lower()=="buy" else "buy"
    amount_base = base_amount(size_eur, price_ref, base_decimals=8)
    if amount_base <= 0:
        raise RuntimeError("amount_base=0 (price_ref?)")
    return bv.placeOrder(market, side_close, "market", {"amount": f"{amount_base:.8f}"})

def get_price(market: str) -> Optional[float]:
    try:
        tp = bv.tickerPrice(market)
        p = tp.get("price")
        return float(p) if p is not None else None
    except Exception as e:
        log_event("WARN", f"tickerPrice fail {e}", "guard", {"market":market})
        return None

def guard_one_open(msg_id: str, fields: Dict[str,str]):
    market   = fields.get("market")
    side     = (fields.get("side") or "").lower()
    size_eur = to_float(fields.get("size_eur"))
    entry    = to_float(fields.get("price"))  # referentie; mag 0 zijn → dan live ophalen
    dry_str  = str(fields.get("dry") or fields.get("live") or "").lower()
    is_dry   = dry_str in ("true","1","yes")

    if not market or side not in ("buy","sell") or size_eur<=0 or is_dry:
        r.xack(ORDER_STREAM, GROUP, msg_id)
        return

    tp_pct    = to_float(fields.get("tp_pct"), 0.006)
    sl_pct    = to_float(fields.get("sl_pct"), 0.004)
    trail_pct = to_float(fields.get("trail_pct"), 0.0)

    if entry <= 0:
        p0 = get_price(market)
        if not p0:
            log_event("ERROR","no price at entry","guard",{"market":market})
            r.xack(ORDER_STREAM, GROUP, msg_id)
            return
        entry = p0

    if side=="buy":
        tp_price = entry*(1+tp_pct)
        sl_price = entry*(1-sl_pct)
    else:
        tp_price = entry*(1-tp_pct)
        sl_price = entry*(1+sl_pct)

    trail_active = False
    trail_stop   = None
    max_seen     = None

    log_event("INFO", f"guard start {market}", "guard", {
        "id": msg_id, "entry": f"{entry:.8f}",
        "tp_pct": tp_pct, "sl_pct": sl_pct, "trail_pct": trail_pct
    })

    poll_ms = 200  # 5 Hz is genoeg voor smoke-test
    while running:
        p = get_price(market)
        if not p:
            time.sleep(poll_ms/1000.0)
            continue

        if side=="buy" and trail_pct>0:
            max_seen = p if max_seen is None else max(max_seen, p)
            if (not trail_active) and p >= entry*(1+trail_pct):
                trail_active = True
            if trail_active and max_seen is not None:
                trail_stop = max_seen*(1-trail_pct)

        # Beslis
        should_close = False
        reason = None
        if side=="buy":
            if p >= tp_price:
                should_close=True; reason="TP"
            elif p <= sl_price:
                should_close=True; reason="SL"
            elif trail_active and trail_stop is not None and p <= trail_stop:
                should_close=True; reason="TRAIL"

        if should_close:
            try:
                o = close_market(market, side, p, size_eur)
                log_event("INFO", f"guard_close {market} {reason} @{p:.8f}", "guard", {
                    "order_id": o.get("orderId"), "pos_side": side, "exit_price": f"{p:.8f}", "reason": reason
                })
            except Exception as e:
                log_event("ERROR", f"close_failed {e}", "guard", {"market":market})
            r.xack(ORDER_STREAM, GROUP, msg_id)
            return

        time.sleep(poll_ms/1000.0)

def ensure_group():
    try:
        r.xgroup_create(ORDER_STREAM, GROUP, id="$", mkstream=True)
    except Exception:
        pass

def loop():
    ensure_group()
    while running:
        try:
            resp = r.xreadgroup(GROUP, CONSUMER, {ORDER_STREAM: ">"}, count=10, block=5000)
        except Exception as e:
            log_event("ERROR", f"xreadgroup {e}", "guard"); time.sleep(0.5); continue
        if not resp:
            continue
        for _, msgs in resp:
            for msg_id, fields in msgs:
                try:
                    guard_one_open(msg_id, fields)
                except Exception as e:
                    log_event("ERROR", f"guard_one_open {e}", "guard", {"id": msg_id})
                    r.xack(ORDER_STREAM, GROUP, msg_id)

if __name__=="__main__":
    log_event("INFO", f"guard start stream={ORDER_STREAM}", "guard", {"version":"order_guard v2"})
    loop()
    log_event("INFO", "guard stop", "guard")
