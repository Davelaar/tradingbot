#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
order_guard_virtual.py — virtuele AI guard (TP/SL/Trail + metrics + concurrency lock)

Gedrag:
- 1 virtuele positie per market (virtpos:<market>) met stapelende BUY-fills
- Altijd max 1 live TP-limit order; SL + trailing virtueel in loop
- Bij SL/trail trigger: TP cancel → market-sell (één shot) → positie reset
- Concurrency: Redis SETNX lock: lock:guard:<market> (met TTL en renewal)

ENV:
- MARKET=GLMR-EUR (of via template trading-guard@.service)
- REDIS_URL=redis://127.0.0.1:6379/0
- GUARD_ALLOW_LIVE=true|false
- TAKE_PROFIT_PCT=0.008
- STOP_LOSS_PCT=0.006
- TRAIL_SL_PCT=0.004
- GUARD_POLL_SEC=0.5
- PROM_PORT=9105   # per instance; via /etc/trading/guard/%i.env

Metrics: :PROM_PORT, best-effort (geen crash bij poortconflict)
"""

from __future__ import annotations
import os, sys, time, json, math, signal, traceback, threading
from typing import Optional, Dict, Any

# ---------- ENV ----------
MARKET           = os.getenv("MARKET", "").strip()
REDIS_URL        = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
ALLOW_LIVE       = os.getenv("GUARD_ALLOW_LIVE", "true").lower() == "true"
TAKE_PROFIT_PCT  = float(os.getenv("TAKE_PROFIT_PCT", "0.008"))
STOP_LOSS_PCT    = float(os.getenv("STOP_LOSS_PCT", "0.006"))
TRAIL_SL_PCT     = float(os.getenv("TRAIL_SL_PCT", "0.004"))
POLL_SEC         = float(os.getenv("GUARD_POLL_SEC", "0.5"))
PROM_PORT        = int(os.getenv("PROM_PORT", "9105"))
LOCK_TTL_SEC     = 10

if not MARKET:
    print("ERROR: MARKET niet gezet (ENV MARKET of --market <PAIR>).", file=sys.stderr)
    sys.exit(2)

# ---------- Deps ----------
try:
    from redis import Redis
except Exception as e:
    print(f"[guard {MARKET}] FATAL: python-redis ontbreekt: {e}", file=sys.stderr)
    sys.exit(1)

# Bitvavo (verwacht dat de unit ENV al klaarzet)
try:
    from python_bitvavo_api.bitvavo import Bitvavo
except Exception as e:
    print(f"[guard {MARKET}] FATAL: python_bitvavo_api ontbreekt: {e}", file=sys.stderr)
    sys.exit(1)

# Metrics: best-effort
_metrics_enabled = True
try:
    from prometheus_client import start_http_server, Gauge, Counter
except Exception:
    _metrics_enabled = False
    class _N:
        def __init__(self,*a,**k): pass
        def labels(self,*a,**k): return self
        def set(self,*a,**k): pass
        def inc(self,*a,**k): pass
    def start_http_server(*a,**k): pass
    Gauge = Counter = _N

g_positions_open   = Gauge("guard_positions_open", "Virtuele posities open", ["market"])
g_tp_open          = Gauge("guard_tp_orders_open", "Actieve TP-limit orders", ["market"])
c_sl_triggers      = Counter("guard_sl_triggers_total", "Aantal SL/trail triggers", ["market"])
c_market_sells     = Counter("guard_market_sells_total", "Aantal uitgevoerde market-sells", ["market"])
c_errors           = Counter("guard_errors_total", "Fouten", ["market","stage"])

def start_metrics():
    global _metrics_enabled
    if not _metrics_enabled:
        print(f"[guard {MARKET}] metrics uit (module niet beschikbaar).")
        return
    try:
        start_http_server(PROM_PORT)
        print(f"[guard {MARKET}] metrics op :{PROM_PORT}")
    except Exception as e:
        # nooit crashen op poort-conflict
        _metrics_enabled = False
        print(f"[guard {MARKET}] metrics uit (poort {PROM_PORT} in gebruik): {e}", file=sys.stderr)

# ---------- Infra ----------
r = Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
bv = Bitvavo({})  # keys via ENV (bitvavo lib leest zelf env vars BITVAVO_API_KEY/SECRET of je unit zet ze)

LOCK_KEY = f"lock:guard:{MARKET}"
VIRTKEY  = f"virtpos:{MARKET}"

_stop = threading.Event()
def _signal_handler(signum, frame):
    _stop.set()
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ---------- Helpers ----------
def _with_lock() -> bool:
    try:
        ok = r.set(LOCK_KEY, os.getpid(), nx=True, ex=LOCK_TTL_SEC)
        return bool(ok)
    except Exception as e:
        c_errors.labels(MARKET, "redis_lock").inc()
        print(f"[guard {MARKET}] lock fout: {e}", file=sys.stderr)
        return False

def _renew_lock():
    try:
        r.expire(LOCK_KEY, LOCK_TTL_SEC)
    except Exception:
        pass

def _read_virt() -> Dict[str, Any]:
    try:
        raw = r.get(VIRTKEY)
        if not raw:
            return {"qty": 0.0, "avg": 0.0, "peak": 0.0, "tpOrderId": "", "lastPx": 0.0}
        return json.loads(raw)
    except Exception:
        return {"qty": 0.0, "avg": 0.0, "peak": 0.0, "tpOrderId": "", "lastPx": 0.0}

def _write_virt(v: Dict[str, Any]):
    try:
        r.set(VIRTKEY, json.dumps(v), ex=7*24*3600)
    except Exception as e:
        c_errors.labels(MARKET, "redis_write").inc()
        print(f"[guard {MARKET}] virt write fout: {e}", file=sys.stderr)

def _reset_virt():
    _write_virt({"qty": 0.0, "avg": 0.0, "peak": 0.0, "tpOrderId": "", "lastPx": 0.0})

def _now_price() -> Optional[float]:
    try:
        t = bv.tickerPrice(MARKET)
        return float(t.get("price"))
    except Exception as e:
        c_errors.labels(MARKET, "ticker").inc()
        return None

def _place_tp_limit(qty: float, limit_px: float) -> Optional[str]:
    if not ALLOW_LIVE:
        return "dry-run"
    try:
        # Bitvavo API: create order
        o = bv.placeOrder(MARKET, "sell", "limit", {"amount": str(qty), "price": str(limit_px), "timeInForce": "GTC"})
        return o.get("orderId")
    except Exception as e:
        c_errors.labels(MARKET, "place_tp").inc()
        print(f"[guard {MARKET}] TP place fout: {e}", file=sys.stderr)
        return None

def _cancel_order(order_id: str):
    if not order_id:
        return
    if not ALLOW_LIVE:
        return
    try:
        bv.cancelOrder(MARKET, order_id)
    except Exception:
        pass

def _market_sell(qty: float) -> bool:
    if qty <= 0:
        return True
    if not ALLOW_LIVE:
        print(f"[guard {MARKET}] DRY market-sell {qty}")
        return True
    try:
        bv.placeOrder(MARKET, "sell", "market", {"amount": str(qty)})
        return True
    except Exception as e:
        c_errors.labels(MARKET, "market_sell").inc()
        print(f"[guard {MARKET}] market-sell fout: {e}", file=sys.stderr)
        return False

# ---------- Core loop ----------
def main() -> int:
    start_metrics()

    # Init virt pos
    vp = _read_virt()

    # Lock: één guard per market
    if not _with_lock():
        print(f"[guard {MARKET}] lock bestaat al; andere instance actief?")
        return 0

    print(f"[guard {MARKET}] START (allow_live={ALLOW_LIVE}, tp={TAKE_PROFIT_PCT}, sl={STOP_LOSS_PCT}, trail={TRAIL_SL_PCT}, poll={POLL_SEC}s)")

    last_lock = time.time()
    tp_live_open = False

    while not _stop.is_set():
        # lock renewal
        if time.time() - last_lock > (LOCK_TTL_SEC/2):
            _renew_lock()
            last_lock = time.time()

        # Huidige prijs
        px = _now_price()
        if px is None or px <= 0:
            time.sleep(POLL_SEC)
            continue

        # Lees laatste fills (optioneel: hier kun je ws fills streamen; we laten het simpel: enkel virt-set)
        # Als elders BUY is gevuld, verwacht je dat qty/avg extern gezet wordt; of we zouden hier recent fills ophalen.

        # Metrics: state
        try:
            g_positions_open.labels(MARKET).set(1.0 if (vp.get("qty",0)>0) else 0.0)
            g_tp_open.labels(MARKET).set(1.0 if (vp.get("tpOrderId") or "") else 0.0)
        except Exception:
            pass

        qty = float(vp.get("qty", 0.0) or 0.0)
        avg = float(vp.get("avg", 0.0) or 0.0)
        peak = float(vp.get("peak", 0.0) or 0.0)
        tp_id = vp.get("tpOrderId") or ""

        # Als geen positie → niets doen behalve wachten.
        if qty <= 0.0 or avg <= 0.0:
            time.sleep(POLL_SEC)
            continue

        # Peak bijwerken
        if px > peak:
            peak = px
            vp["peak"] = peak
            _write_virt(vp)

        # TP-level (live limit):  avg * (1 + TAKE_PROFIT_PCT)
        tp_px = round(avg * (1.0 + TAKE_PROFIT_PCT), 8)
        # SL-level (virtueel):    max(avg * (1 - STOP_LOSS_PCT), peak * (1 - TRAIL_SL_PCT))
        hard_sl = avg * (1.0 - STOP_LOSS_PCT)
        trail_sl = peak * (1.0 - TRAIL_SL_PCT)
        sl_px = max(hard_sl, trail_sl)

        # TP order aanwezig?
        if not tp_id:
            # plaats nieuwe TP
            oid = _place_tp_limit(qty, tp_px)
            if oid:
                vp["tpOrderId"] = oid
                _write_virt(vp)
                tp_live_open = True
        else:
            tp_live_open = True

        # SL/trailing check
        if px <= sl_px:
            c_sl_triggers.labels(MARKET).inc()
            # cancel TP → market sell → reset
            if tp_id:
                _cancel_order(tp_id)
            ok = _market_sell(qty)
            if ok:
                c_market_sells.labels(MARKET).inc()
                _reset_virt()
                vp = _read_virt()
                tp_live_open = False
                # kleine pauze om dubbele sells te voorkomen
                time.sleep(max(POLL_SEC, 0.5))
                continue

        # Bewaar lastPx voor inzicht
        vp["lastPx"] = px
        _write_virt(vp)

        time.sleep(POLL_SEC)

    print(f"[guard {MARKET}] STOP")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        c_errors.labels(MARKET, "main_crash").inc()
        traceback.print_exc()
        sys.exit(1)