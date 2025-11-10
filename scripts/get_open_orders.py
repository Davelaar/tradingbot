#!/usr/bin/env python3
# get_open_orders.py — haalt open orders op volgens Bitvavo docs

import os, sys, time, hmac, hashlib, json, urllib.request, urllib.error

def load_env(path):
    try:
        with open(path, "r") as f:
            for line in f:
                line=line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k,v=line.split("=",1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass

def private_request(method, endpoint, params=None):
    params = params or {}
    timestamp = str(int(time.time()*1000))
    qs = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    path = endpoint + (("?" + qs) if qs else "")
    payload = timestamp + method + endpoint + (qs and ("?" + qs) or "")
    secret = os.getenv("BITVAVO_API_SECRET","")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    url = "https://api.bitvavo.com/v2" + path
    headers = {
        "Content-Type": "application/json",
        "Bitvavo-Access-Key": os.getenv("BITVAVO_API_KEY",""),
        "Bitvavo-Access-Signature": signature,
        "Bitvavo-Access-Timestamp": timestamp,
        "Bitvavo-Access-Window": "10000"
    }
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": e.reason}
    except Exception as e:
        return 0, {"error": str(e)}

def main():
    load_env("/srv/trading/.env.bitvavo")
    market = sys.argv[1] if len(sys.argv)>1 else None
    params = {}
    if market:
        params["market"] = market
    status, resp = private_request("GET", "/ordersOpen", params)
    print("[status]", status)
    if status != 200:
        print("[error_resp]", resp)
        sys.exit(1)
    if not isinstance(resp, list):
        print("[unexpected_response]", resp)
        sys.exit(1)
    print("[openorders.count]", len(resp))
    for o in resp:
        print("[open]", json.dumps({
            "orderId": o.get("orderId"),
            "market": o.get("market"),
            "side": o.get("side"),
            "orderType": o.get("orderType"),
            "status": o.get("status"),
            "price": o.get("price"),
            "amount": o.get("amount"),
            "amountRemaining": o.get("amountRemaining")
        }, separators=(",",":")))

if __name__=="__main__":
    main()