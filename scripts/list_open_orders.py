#!/usr/bin/env python3
# list_open_orders.py — betrouwbare SDK-check van open orders per market

import os, sys, json

def load_env(path):
    try:
        with open(path, "r") as f:
            for line in f:
                line=line.strip()
                if not line or line.startswith("#") or "=" not in line: 
                    continue
                k,v=line.split("=",1)
                os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))
    except FileNotFoundError:
        pass

def main():
    # env inladen (BITVAVO_API_KEY / BITVAVO_API_SECRET)
    load_env("/srv/trading/.env.bitvavo")
    api_key = os.getenv("BITVAVO_API_KEY","")
    api_sec = os.getenv("BITVAVO_API_SECRET","")
    if not api_key or not api_sec:
        print("[error] missing credentials in .env.bitvavo")
        sys.exit(1)

    # market via arg (default ICP-EUR)
    market = sys.argv[1] if len(sys.argv) > 1 else "ICP-EUR"

    # SDK
    try:
        from python_bitvavo_api.bitvavo import Bitvavo
    except ImportError:
        print("[error] python_bitvavo_api not installed")
        sys.exit(1)

    bv = Bitvavo({"APIKEY": api_key, "APISECRET": api_sec})

    # Open orders (status=new) — strikt SDK
    try:
        res = bv.getOrders({"market": market, "status": "new", "limit": 100})
    except Exception as e:
        print("[error] getOrders failed:", str(e))
        sys.exit(1)

    orders = res if isinstance(res, list) else []
    print(f"[market] {market}")
    print(f"[openorders.count] {len(orders)}")

    for o in orders:
        # alleen veilige velden loggen
        out = {
            "orderId": o.get("orderId"),
            "side": o.get("side"),
            "type": o.get("orderType"),
            "status": o.get("status"),
            "price": o.get("price"),
            "amount": o.get("amount"),
            "amountRemaining": o.get("amountRemaining"),
        }
        print("[open]", json.dumps(out, separators=(",",":")))

if __name__ == "__main__":
    main()