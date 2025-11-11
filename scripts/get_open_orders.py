#!/usr/bin/env python3
"""
get_open_orders.py — WS-first open orders (helpers) met REST-fallback
- Gebruikt uitsluitend scripts.helpers.orders.list_open_orders()
- Output exact volgens blueprint:
  [status] 200
  [openorders.count] N
  [open] {...}
  (bij fout)
  [status] 0
  [error] ...
"""
from __future__ import annotations
import sys, os, json

# Zorg dat /srv/trading altijd op het importpad staat (ongeacht huidige werkdir)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.helpers.orders import list_open_orders, _load_env  # type: ignore

def main() -> None:
    _load_env()
    market = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        rows = list_open_orders(market)
        print("[status] 200")
        print(f"[openorders.count] {len(rows)}")
        for r in rows:
            print("[open]", json.dumps(r, separators=(",",":")))
    except Exception as e:
        print("[status] 0")
        print("[error]", str(e))

if __name__ == "__main__":
    main()