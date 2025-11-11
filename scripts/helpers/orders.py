#!/usr/bin/env python3
"""
helpers/orders.py — Open orders ophalen (WS-first, REST-fallback)
- WS-first: Bitvavo.newWebsocket().ordersOpen({market?}, callback) → snapshot van ALLE open orders
- REST-fallback: GET /v2/ordersOpen[?market=...] met correcte HMAC-signing
- market=None  => alle markten
- market="GLMR-EUR" => alleen die markt
- CLI-output (bij __main__) conformeert aan blueprint
"""
from __future__ import annotations
import os, time, hmac, hashlib, json, urllib.request, urllib.error
from typing import List, Optional, Dict, Any

BITVAVO_BASE = "https://api.bitvavo.com"
ENV_FILE = "/srv/trading/.env.bitvavo"

# ========== ENV ==========

def _load_env(path: str = ENV_FILE) -> None:
    try:
        with open(path, "r") as f:
            for line in f:
                line=line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k,v=line.split("=",1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass

# ========== NORMALISATIE ==========

def _normalize_orders(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for o in rows:
        out.append({
            "orderId": o.get("orderId"),
            "market": o.get("market"),
            "side": o.get("side"),
            "orderType": o.get("orderType"),
            "status": o.get("status") or "new",
            "price": o.get("price"),
            "amount": o.get("amount"),
            "amountRemaining": o.get("amountRemaining"),
        })
    return out

# ========== REST-FALLBACK ==========

def _qs(params: Dict[str, Any]) -> str:
    if not params:
        return ""
    parts = []
    for k in sorted(params.keys()):
        parts.append(f"{k}={params[k]}")
    return "&".join(parts)

def _rest_list_open_orders(market: Optional[str]) -> List[Dict[str, Any]]:
    key = os.getenv("BITVAVO_API_KEY","").strip()
    sec = os.getenv("BITVAVO_API_SECRET","").strip()
    if not key or not sec:
        raise RuntimeError("Missing BITVAVO_API_KEY / BITVAVO_API_SECRET")

    params: Dict[str, Any] = {}
    if market:
        params["market"] = market

    qs = _qs(params)
    path = "/v2/ordersOpen" + (("?" + qs) if qs else "")
    ts = str(int(time.time()*1000))
    payload = ts + "GET" + path
    sig = hmac.new(sec.encode(), payload.encode(), hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Bitvavo-Access-Key": key,
        "Bitvavo-Access-Signature": sig,
        "Bitvavo-Access-Timestamp": ts,
        "Bitvavo-Access-Window": "60000",
        "User-Agent": "tradingbot-openorders/1.5"
    }

    req = urllib.request.Request(BITVAVO_BASE + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode(errors="ignore")
        except: pass
        raise RuntimeError(f"HTTP {e.code}: {body}")

    if not isinstance(data, list):
        return []
    return _normalize_orders(data)

# ========== WEBSOCKET (WS-FIRST) ==========

def _ws_list_open_orders(market: Optional[str], timeout_sec: float = 5.0) -> List[Dict[str, Any]]:
    """
    Probeert via Bitvavo WebSocket alle open orders in één snapshot op te halen.
    Retourneert [] bij fout/timeout (caller valt dan terug op REST).
    """
    try:
        from python_bitvavo_api.bitvavo import Bitvavo  # type: ignore
    except Exception:
        return []

    key = os.getenv("BITVAVO_API_KEY","").strip()
    sec = os.getenv("BITVAVO_API_SECRET","").strip()
    if not key or not sec:
        return []

    bv = Bitvavo({
        "APIKEY": key,
        "APISECRET": sec,
        "RESTURL": "https://api.bitvavo.com/v2",
        "WSURL":   "wss://ws.bitvavo.com/v2/",
        "ACCESSWINDOW": 60000,
        "DEBUGGING": False
    })
    ws = bv.newWebsocket()

    results: List[Dict[str, Any]] = []
    finished = {"done": False}

    def ok_cb(resp):
        # resp kan list zijn of dict met 'orders'
        try:
            if isinstance(resp, list):
                for it in resp:
                    if isinstance(it, dict):
                        results.append(it)
            elif isinstance(resp, dict):
                if isinstance(resp.get("orders"), list):
                    for it in resp["orders"]:
                        if isinstance(it, dict):
                            results.append(it)
        finally:
            finished["done"] = True

    def err_cb(err):
        finished["done"] = True

    ws.setErrorCallback(err_cb)

    params: Dict[str, Any] = {}
    if market:
        params["market"] = market

    try:
        ws.ordersOpen(params, ok_cb)
    except Exception:
        return []

    t0 = time.time()
    while time.time() - t0 < timeout_sec and not finished["done"]:
        time.sleep(0.05)

    try:
        ws.closeSocket()
    except Exception:
        pass

    if not results:
        return []
    return _normalize_orders(results)

# ========== PUBLIEKE API ==========

def list_open_orders(market: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Geef ALLE open orders (alle markten of gefilterd).
    Strategie: WS-first → REST-fallback.
    """
    _load_env()
    rows = _ws_list_open_orders(market)
    if rows:
        return rows
    return _rest_list_open_orders(market)

# ========== CLI ==========
if __name__ == "__main__":
    import sys
    _load_env()
    mk = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        rows = list_open_orders(mk)
        print("[status] 200")
        print(f"[openorders.count] {len(rows)}")
        for r in rows:
            print("[open]", json.dumps(r, separators=(",",":")))
    except Exception as e:
        print("[status] 0")
        print("[error]", str(e))