#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fills_sim.py — v3 (2025-10-30)
Dry-run fills simulator:
- TP/SL + Trailing Stop (zelfde fee-model als eerder)
- Latency/jitter + slippage simulatie
- Prometheus metrics export (HTTP) met Redis-fallback

Streams:
- In:  orders:shadow  (OPEN events uit core; we lezen alleen action=OPEN, dry=true)
- In:  candles:1m     (simuleert prijsbewegingen per market)
- Out: positions:open  (HSET pos_id -> JSON)
- Out: positions:closed(HSET pos_id -> JSON)
- Out: pnl:realized_eur_total (SET), pnl:realized_eur (HSET per market)
- Out: trading:events (INFO/WARN/ERROR/DEBUG)

Consumer groups (worden aangemaakt indien nodig):
- orders:shadow -> sim_orders
- candles:1m   -> sim_candles
"""

import os, time, random, threading
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional
from redis import Redis

try:
    from prometheus_client import Gauge, Counter, start_http_server
    HAVE_PROM = True
except Exception:
    HAVE_PROM = False

VERSION = "fills_sim 2025-10-30 v3"

# ---------- helpers ----------
def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def d(x) -> Decimal:
    return Decimal(str(x))

def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def q8(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)).split("#",1)[0].strip())
    except Exception:
        return default

def env_float(name, default):
    try:
        return float(os.getenv(name, str(default)).split("#",1)[0].strip())
    except Exception:
        return default

def env_str(name, default):
    v = os.getenv(name)
    return (v.split("#",1)[0].strip() if v is not None else default)

# ---------- config ----------
REDIS_URL = env_str("REDIS_URL", "redis://127.0.0.1:6379/0")
ORDER_STREAM = env_str("ORDER_OUTBOX_STREAM", "orders:shadow")
CANDLE_STREAM = env_str("CANDLE_STREAM", "candles:1m")
EVENT_STREAM  = env_str("EVENT_STREAM", "trading:events")

GRP_ORDERS   = env_str("SIM_GROUP_ORDERS",  "sim_orders")
GRP_CANDLES  = env_str("SIM_GROUP_CANDLES", "sim_candles")
CONSUMER     = env_str("SIM_CONSUMER",      "sim")

# Fees (in bps). We lezen eerst account/market keys; als die ontbreken gebruiken we defaults
DEF_MAKER_BPS = env_float("SIM_DEFAULT_MAKER_BPS", 15.0)   # 0.15%
DEF_TAKER_BPS = env_float("SIM_DEFAULT_TAKER_BPS", 25.0)   # 0.25%

# Slippage/latency
SLIP_BPS_MAX  = env_float("SIM_MAX_SLIPPAGE_BPS", 2.0)     # max absoluut in bps
LAT_MS_MIN    = env_int("SIM_LATENCY_MS_MIN", 20)
LAT_MS_MAX    = env_int("SIM_LATENCY_MS_MAX", 180)

# Prometheus
PROM_PORT     = env_int("SIM_PROM_PORT", 9109)

# intern
r = Redis.from_url(REDIS_URL, decode_responses=True)

# Metrics (prometheus) + Redis-fallback keys
if HAVE_PROM:
    G_PNL_TOTAL     = Gauge("pnl_realized_eur_total", "Total realized PnL in EUR")
    G_POS_OPEN      = Gauge("positions_open", "Number of open positions")
    G_ORD_OUT_LEN   = Gauge("orders_outbox_len", "Length of orders:shadow stream (approx)")
    C_POS_CLOSED    = Counter("positions_closed_total", "Closed positions count")
else:
    G_PNL_TOTAL = G_POS_OPEN = G_ORD_OUT_LEN = C_POS_CLOSED = None

def log_event(lvl: str, msg: str, where: str, extra: Dict[str,Any]=None):
    data = {"ts": now_iso(), "lvl": lvl, "msg": msg, "where": where, "version": VERSION}
    if extra:
        data.update(extra)
    r.xadd(EVENT_STREAM, data)

def ensure_groups():
    try:
        r.xgroup_create(ORDER_STREAM, GRP_ORDERS, id="0-0", mkstream=True)
    except Exception:
        pass
    try:
        r.xgroup_create(CANDLE_STREAM, GRP_CANDLES, id="0-0", mkstream=True)
    except Exception:
        pass

# ---------- fees ----------
def get_bps_for(market: str) -> (Decimal, Decimal):
    """Return (maker_bps, taker_bps) as Decimal from Redis keys or defaults."""
    mk = r.get("fees:account:maker_bps")
    tk = r.get("fees:account:taker_bps")
    mk_bps = d(mk) if mk else d(DEF_MAKER_BPS)
    tk_bps = d(tk) if tk else d(DEF_TAKER_BPS)
    # market overrides (if present)
    mk_m = r.get(f"fees:market:{market}:maker_bps")
    tk_m = r.get(f"fees:market:{market}:taker_bps")
    if mk_m: mk_bps = d(mk_m)
    if tk_m: tk_bps = d(tk_m)
    return mk_bps, tk_bps

# ---------- state ----------
# positions:open  -> HSET pos_id JSON
# positions:closed-> HSET pos_id JSON
# pnl:realized_eur_total -> SET
# pnl:realized_eur       -> HSET market -> value
def load_pnl_total() -> Decimal:
    v = r.get("pnl:realized_eur_total")
    return d(v) if v else d("0")

def save_pnl_total(x: Decimal):
    r.set("pnl:realized_eur_total", str(q2(x)))
    if HAVE_PROM:
        try: G_PNL_TOTAL.set(float(q2(x)))
        except Exception: pass

def incr_pnl(market: str, delta: Decimal):
    total = load_pnl_total() + delta
    save_pnl_total(total)
    cur = r.hget("pnl:realized_eur", market)
    curd = d(cur) if cur else d("0")
    r.hset("pnl:realized_eur", market, str(q2(curd + delta)))

def pos_key_open():   return "positions:open"
def pos_key_closed(): return "positions:closed"

def pos_open_count() -> int:
    return int(r.hlen(pos_key_open()))

def set_metrics_housekeeping():
    if HAVE_PROM:
        try:
            G_POS_OPEN.set(pos_open_count())
            # not exact stream length → inexpensive approximation: XLEN
            try:
                out_len = r.xlen(ORDER_STREAM)
                G_ORD_OUT_LEN.set(float(out_len))
            except Exception:
                pass
        except Exception:
            pass

# ---------- trailing stop helpers ----------
def calc_levels(side: str, entry: Decimal, tp_pct: Decimal, sl_pct: Decimal):
    if side == "buy":
        tp_price = entry * (d(1) + tp_pct)
        sl_price = entry * (d(1) - sl_pct)
    else:
        tp_price = entry * (d(1) - tp_pct)
        sl_price = entry * (d(1) + sl_pct)
    return (q8(tp_price), q8(sl_price))

def apply_slippage(price: Decimal, side: str) -> Decimal:
    """Random symmetric slippage within ±SLIP_BPS_MAX."""
    bps = d(random.uniform(-SLIP_BPS_MAX, SLIP_BPS_MAX)) / d(1e4)
    # buy -> price slightly worse if bps>0; sell -> opposite; we just apply factor uniformly
    return q8(price * (d(1) + bps))

def maybe_latency():
    delay = random.uniform(LAT_MS_MIN, LAT_MS_MAX) / 1000.0
    time.sleep(delay)
    return delay

def close_position(pos: Dict[str,Any], reason: str, exit_px: Decimal, maker_close: bool):
    """Finalize close: fees, pnl, move to closed."""
    mk_bps, tk_bps = get_bps_for(pos["market"])
    entry   = d(pos["entry"])
    size_eur= d(pos["size_eur"])
    side    = pos["side"]
    # fees: buy fee was charged at open as taker (we simulate a market open)
    buy_fee_eur = (size_eur * tk_bps / d(1e4))
    sell_fee_eur= (size_eur * (mk_bps if maker_close else tk_bps) / d(1e4))

    if side == "buy":
        pnl = (exit_px - entry) / entry * size_eur - buy_fee_eur - sell_fee_eur
    else:
        pnl = (entry - exit_px) / entry * size_eur - buy_fee_eur - sell_fee_eur

    pos["ts_close"] = now_iso()
    pos["exit_price"] = float(q8(exit_px))
    pos["reason"] = reason
    pos["sell_fee_eur"] = float(q2(sell_fee_eur))
    pos["pnl_eur"] = float(q2(pnl))

    # write closed, remove open
    pos_id = pos["pos_id"]
    r.hset(pos_key_closed(), pos_id, __import__("orjson").dumps(pos).decode())
    r.hdel(pos_key_open(), pos_id)

    incr_pnl(pos["market"], q2(pnl))
    set_metrics_housekeeping()
    if HAVE_PROM:
        try: C_POS_CLOSED.inc()
        except Exception: pass

    log_event("INFO", f"sim_close {pos['market']} {reason} @ {q8(exit_px):f} €, pnl={q2(pnl)}€", "fills_sim",
              {"pos_id": pos_id})

def handle_open(order: Dict[str,str]):
    """
    Order fields we expect (strings):
    - action=OPEN, market, side, price, size_eur, tp_pct, sl_pct, mode, signal_id, dry
    - OPTIONAL: trail_pct
    """
    market   = order.get("market","")
    side     = order.get("side","").lower()
    entry    = d(order.get("price","0"))
    size_eur = d(order.get("size_eur","0"))
    tp_pct   = d(order.get("tp_pct","0"))
    sl_pct   = d(order.get("sl_pct","0"))
    trail_pct= d(order.get("trail_pct","0"))

    if not market or side not in ("buy","sell") or entry<=0 or size_eur<=0:
        return

    tp_price, sl_price = calc_levels(side, entry, tp_pct, sl_pct)

    pos_id = f"{market}:{order.get('signal_id','SIM')}"
    pos = {
        "pos_id": pos_id,
        "market": market,
        "side": side,
        "entry": float(q8(entry)),
        "size_eur": float(q2(size_eur)),
        "tp_pct": float(tp_pct),
        "sl_pct": float(sl_pct),
        "tp_price": float(tp_price),
        "sl_price": float(sl_price),
        "mode": order.get("mode","fixed"),
        "dry": order.get("dry","true") in ("true","1","yes","on"),
        "signal_id": order.get("signal_id",""),
        "ts_open": now_iso(),
        # trailing
        "trail_pct": float(trail_pct),
        "trail_active": False,
        "trail_stop": None,  # float when active
    }

    # buy fee at open (taker). We store for completeness (pnl calc recomputes anyway)
    mk_bps, tk_bps = get_bps_for(market)
    pos["buy_fee_eur"] = float(q2(d(pos["size_eur"]) * tk_bps / d(1e4)))

    r.hset(pos_key_open(), pos_id, __import__("orjson").dumps(pos).decode())
    set_metrics_housekeeping()
    log_event("INFO", f"sim_open {pos_id} @ {q8(entry):f} €", "fills_sim")

def on_candle(c: Dict[str,str]):
    market = c.get("market","")
    if not market: return
    try:
        o = d(c.get("o","0")); h = d(c.get("h","0")); l = d(c.get("l","0")); close = d(c.get("c","0"))
    except Exception:
        return

    # scan open positions of this market
    opens = r.hgetall(pos_key_open())
    for pos_id, blob in opens.items():
        if not pos_id.startswith(market + ":"):
            continue
        pos = __import__("orjson").loads(blob)

        side = pos["side"]
        entry = d(pos["entry"])
        tp_px = d(pos["tp_price"])
        sl_px = d(pos["sl_price"])
        trail_pct = d(pos.get("trail_pct", 0.0))

        # --- trailing activation/update (alleen wanneer koers gunstig beweegt) ---
        if float(trail_pct) > 0.0:
            if side == "buy":
                # activeer zodra high >= entry*(1+trail_pct)
                activate_px = entry * (d(1) + trail_pct)
                if h >= activate_px:
                    # trail stop = high*(1 - trail_pct); monotone non-decreasing
                    new_trail = q8(h * (d(1) - trail_pct))
                    prev_trail = d(str(pos["trail_stop"])) if pos.get("trail_stop") is not None else None
                    if prev_trail is None or new_trail > prev_trail:
                        pos["trail_active"] = True
                        pos["trail_stop"] = float(new_trail)
            else:  # sell
                activate_px = entry * (d(1) - trail_pct)
                if l <= activate_px:
                    new_trail = q8(l * (d(1) + trail_pct))
                    prev_trail = d(str(pos["trail_stop"])) if pos.get("trail_stop") is not None else None
                    if prev_trail is None or new_trail < prev_trail:
                        pos["trail_active"] = True
                        pos["trail_stop"] = float(new_trail)

        # --- close conditions (buy) ---
        closed = False
        maker_close = False
        exit_px = None

        if side == "buy":
            # volgorde: SL (l<=sl), Trail (l<=trail_stop), TP (h>=tp)
            if l <= sl_px:
                exit_px = sl_px
                maker_close = False
                reason = "SL"
            elif pos.get("trail_active") and pos.get("trail_stop") is not None and l <= d(pos["trail_stop"]):
                exit_px = d(pos["trail_stop"])
                maker_close = False
                reason = "TRAIL"
            elif h >= tp_px:
                exit_px = tp_px
                maker_close = True  # TP via maker
                reason = "TP"
        else:
            # voor sells (niet gebruikt nu), spiegel logica
            if h >= sl_px:
                exit_px = sl_px; maker_close = False; reason = "SL"
            elif pos.get("trail_active") and pos.get("trail_stop") is not None and h >= d(pos["trail_stop"]):
                exit_px = d(pos["trail_stop"]); maker_close = False; reason = "TRAIL"
            elif l <= tp_px:
                exit_px = tp_px; maker_close = True; reason = "TP"

        if exit_px is not None:
            # latency + slippage
            latency = maybe_latency()
            exit_px_adj = apply_slippage(exit_px, side)
            pos["latency_ms"] = int(latency * 1000)
            close_position(pos, reason, exit_px_adj, maker_close)
            closed = True

        if not closed:
            # als trail_update plaatsvond, pos opnieuw opslaan
            r.hset(pos_key_open(), pos_id, __import__("orjson").dumps(pos).decode())

def loop_orders():
    while True:
        try:
            msgs = r.xreadgroup(GRP_ORDERS, CONSUMER, streams={ORDER_STREAM: ">"}, count=50, block=2000)
            for stream, items in msgs or []:
                for mid, fields in items:
                    f = dict(fields)
                    if f.get("action","") == "OPEN":
                        handle_open(f)
                    r.xack(ORDER_STREAM, GRP_ORDERS, mid)
        except Exception as e:
            log_event("ERROR", str(e), "sim_orders_loop")
            time.sleep(0.5)

def loop_candles():
    while True:
        try:
            msgs = r.xreadgroup(GRP_CANDLES, CONSUMER, streams={CANDLE_STREAM: ">"}, count=200, block=2000)
            for stream, items in msgs or []:
                for mid, fields in items:
                    on_candle(dict(fields))
                    r.xack(CANDLE_STREAM, GRP_CANDLES, mid)
        except Exception as e:
            log_event("ERROR", str(e), "sim_candles_loop")
            time.sleep(0.5)

def main():
    ensure_groups()
    set_metrics_housekeeping()
    if HAVE_PROM:
        try:
            start_http_server(PROM_PORT)
            log_event("INFO", f"prometheus /metrics on :{PROM_PORT}", "fills_sim")
        except Exception as e:
            log_event("WARN", f"prometheus disabled: {e}", "fills_sim")

    t1 = threading.Thread(target=loop_orders, daemon=True)
    t2 = threading.Thread(target=loop_candles, daemon=True)
    t1.start(); t2.start()

    log_event("INFO", "fills_sim started", "fills_sim")
    while True:
        set_metrics_housekeeping()
        time.sleep(5)

if __name__ == "__main__":
    main()