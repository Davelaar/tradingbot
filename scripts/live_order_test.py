#!/usr/bin/env python3
"""
live_order_test.py — selecteert een niet-BTC -EUR pair (via pair_selector.py of fallback),
plaatst een veilige €10 LIMIT BUY (postOnly, zeer lage prijs) en annuleert direct.
OperatorId (1702) wordt vóór het signen toegevoegd (POST: body, DELETE: query).
"""

import os
import sys
import time
import json
import hmac
import hashlib
import decimal
import subprocess
import re
import urllib.request
import urllib.error

BASE = "https://api.bitvavo.com"
OPERATOR_ID = 1702  # vast, door gebruiker opgegeven


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def load_env_file(path: str) -> dict:
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _sorted_qs(params: dict | None) -> str:
    if not params:
        return ""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def http(method: str, endpoint: str, params: dict | None = None, body: dict | None = None,
         auth: bool = True, timeout: int = 15):
    """
    Minimale Bitvavo HTTP-client met exacte HMAC-signing:
      payload = ts + METHOD + endpoint + (?query) + (json body)
    - Voor private POST/PUT: operatorId wordt in de body gezet vóór het signen.
    - Voor private GET/DELETE: operatorId wordt in de query gezet vóór het signen.
    """
    key = os.getenv("BITVAVO_API_KEY")
    sec = os.getenv("BITVAVO_API_SECRET")
    ts = str(int(time.time() * 1000))

    params = dict(params or {})
    body_obj = dict(body or {})

    # === operatorId toevoegen vóór signen ===
    if auth:
        if method in ("POST", "PUT"):
            if "operatorId" not in body_obj:
                body_obj["operatorId"] = OPERATOR_ID
        else:  # GET/DELETE
            if "operatorId" not in params:
                params["operatorId"] = OPERATOR_ID

    # Query-string deterministisch opbouwen (gesorteerd)
    qs = _sorted_qs(params)
    url = BASE + endpoint + (("?" + qs) if qs else "")

    # Body-JSON compact serialiseren vóór het signen
    body_json = None
    if body_obj:
        body_json = json.dumps(body_obj, separators=(",", ":"))

    # Payload exact zoals Bitvavo verwacht
    payload = ts + method + endpoint + (("?" + qs) if qs else "") + (body_json or "")

    headers = {"Content-Type": "application/json"}
    if auth:
        sig = hmac.new(sec.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers.update({
            "Bitvavo-Access-Key": key,
            "Bitvavo-Access-Signature": sig,
            "Bitvavo-Access-Timestamp": ts,
            "Bitvavo-Access-Window": "10000",
        })

    data = body_json.encode() if body_json is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": e.reason}
    except Exception as e:
        return 0, {"error": str(e)}


def pick_pair() -> str | None:
    """
    Kies eerste niet-BTC pair dat op -EUR eindigt.
    Voorkeur via /tradingbot/tools/pair_selector.py, anders fallback-lijst.
    Respecteert PAIRSEL_DENY_BASES (majors/stables/fiat).
    """
    selector = "/srv/trading/tradingbot/tools/pair_selector.py"
    deny = {s.strip().upper() for s in os.getenv(
        "PAIRSEL_DENY_BASES",
        "BTC,ETH,BNB,ADA,SOL,XRP,USDT,USDC,EUR,USD,DAI"
    ).split(",") if s.strip()}

    if os.path.isfile(selector):
        try:
            p = subprocess.Popen(
                [sys.executable, selector],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            start = time.time()
            chosen = None
            while time.time() - start < 12:
                line = p.stdout.readline()
                if not line:
                    break
                m = re.search(r"selected\s*=\s*\[(.+?)\]", line, re.IGNORECASE)
                if m:
                    tokens = [t.strip().strip("\"' ").upper() for t in m.group(1).split(",")]
                    for t in tokens:
                        if t.endswith("-EUR") and "BTC" not in t:
                            base = t.split("-")[0]
                            if base not in deny:
                                chosen = t
                                break
                if chosen:
                    try:
                        p.terminate()
                        p.wait(timeout=2)
                    except Exception:
                        pass
                    return chosen
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass

    # Fallback (géén BTC/majors/stables)
    fallback = ["ICP-EUR", "COTI-EUR", "AAVE-EUR", "ATOM-EUR",
                "ALGO-EUR", "FTM-EUR", "NEAR-EUR", "AR-EUR", "RNDR-EUR"]
    for t in fallback:
        base = t.split("-")[0]
        if base not in deny and base != "BTC":
            return t
    return None


def qdown(val: float, dec: int) -> str:
    q = decimal.Decimal(10) ** -dec
    return str(decimal.Decimal(val).quantize(q, rounding=decimal.ROUND_DOWN))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    # Laad .env.bitvavo expliciet
    for k, v in load_env_file("/srv/trading/.env.bitvavo").items():
        os.environ.setdefault(k, v)

    if not os.getenv("BITVAVO_API_KEY") or not os.getenv("BITVAVO_API_SECRET"):
        print("[error] BITVAVO_API_KEY/SECRET ontbreken")
        sys.exit(1)

    # Publieke check
    st_pub, r_pub = http("GET", "/v2/time", auth=False)
    print("[public:/time]", st_pub, r_pub)

    market = pick_pair()
    if not market:
        print("[selected] NONE")
        sys.exit(1)
    print(f"[selected] {market}")

    # Marktinfo
    st_mk, info = http("GET", "/v2/markets", params={"market": market}, auth=False)
    if st_mk != 200 or not info:
        print("[market-info]", st_mk, info)
        sys.exit(1)
    mi = info[0] if isinstance(info, list) else info

    # Precisievelden (beide varianten ondersteunen)
    prec = mi.get("precision", {}) or {}
    pp = int(mi.get("pricePrecision") or prec.get("price", 2))
    ap = int(mi.get("amountPrecision") or prec.get("amount", 6))
    minQ = float(mi.get("minOrderInQuote", 0) or 0)
    minB = float(mi.get("minOrderInBase", 0) or 0)
    print(f"[market-info] pp={pp} ap={ap} minQuote={minQ} minBase={minB}")

    # Veilige €10 order (lage prijs -> geen fill), precisie & minima respecteren
    decimal.getcontext().prec = 28
    target_eur = max(10.0, minQ if minQ > 0 else 10.0)
    price = max(0.5, 10 ** (-pp))
    amount = target_eur / price
    if minB and amount < minB:
        amount = minB
    price_s = qdown(price, pp)
    amount_s = qdown(amount, ap)

    order = {
        "market": market,
        "side": "buy",
        "orderType": "limit",
        "amount": amount_s,
        "price": price_s,
        "timeInForce": "GTC",
        "postOnly": True
        # operatorId wordt door http(...) in de body gezet vóór signen
    }

    print("[debug:]", json.dumps({"body": order}, separators=(",", ":")))
    st_pl, r_pl = http("POST", "/v2/order", body=order, auth=True)
    print("[place]", st_pl, r_pl)

    if st_pl == 200 and isinstance(r_pl, dict) and r_pl.get("orderId"):
        oid = r_pl["orderId"]
        # operatorId wordt door http(...) als queryparam toegevoegd vóór signen
        st_ca, r_ca = http("DELETE", "/v2/order",
                            params={"market": market, "orderId": oid},
                            auth=True)
        print("[cancel]", st_ca, r_ca)
    else:
        print("[cancel] skipped (no orderId)")