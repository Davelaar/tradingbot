#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metrics_sidecar.py — exposeert trading_* gauges uit Redis op :9110
- trading_pnl_realized_eur_total
- trading_positions_open
- trading_orders_outbox_len
Veilig: read-only; raakt je sim/core niet.
"""
import os, time, sys
from typing import Optional
from redis import Redis
from prometheus_client import Gauge, start_http_server

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
PORT = int(os.getenv("METRICS_PORT", "9110"))

G_PNL  = Gauge("trading_pnl_realized_eur_total", "Sum of realized PnL in EUR")
G_POS  = Gauge("trading_positions_open", "Number of open positions")
G_OUT  = Gauge("trading_orders_outbox_len", "Length of orders:shadow outbox")

def to_float(x: Optional[str]) -> float:
    try: return float(x)
    except: return 0.0

def main():
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    start_http_server(PORT)
    print(f"[metrics_sidecar] /metrics on :{PORT}, redis={REDIS_URL}", flush=True)
    while True:
        try:
            pnl = to_float(r.get("pnl:realized_eur_total"))
        except Exception: pnl = 0.0
        try:
            pos = int(r.hlen("positions:open"))
        except Exception: pos = 0
        try:
            out = int(r.xlen("orders:shadow"))
        except Exception: out = 0
        G_PNL.set(pnl); G_POS.set(pos); G_OUT.set(out)
        time.sleep(3)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
