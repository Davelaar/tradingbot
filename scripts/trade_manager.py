#!/usr/bin/env python3
"""
trade_manager.py — Stap 9.5 (validatie: place + cancel)
- Leest TP/SL uit /srv/trading/.env.trade (defaults TP=1.0%, SL=0.6%)
- Kiest pair via AI hook (/srv/trading/ai/ai_pair_selector.py) als AI_PAIR=1, anders via pair_selector.py
- Plaatst een veilige limit BUY van MAX_NOTIONAL_EUR (postOnly indien POST_ONLY=1) en annuleert direct (validatie-run)
"""
import os, sys, time, json, hmac, hashlib, decimal, subprocess, re, urllib.request, urllib.error

BASE = "https://api.bitvavo.com"
OPERATOR_ID = 1702  # vastgezet

# ---------- env helpers ----------
def load_env_file(path):
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

def getenv_num(name, default):
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default

def getenv_int(name, default):
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default

# ---------- http ----------
def _sorted_qs(params):
    if not params: return ""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))

def http(method, endpoint, params=None, body=None, auth=True, timeout=15):
    key = os.getenv("BITVAVO_API_KEY")
    sec = os.getenv("BITVAVO_API_SECRET")
    ts  = str(int(time.time()*1000))

    params = dict(params or {})
    body_o = dict(body or {})

    # operatorId vóór signen toevoegen
    if auth:
        if method in ("POST","PUT"):
            body_o.setdefault("operatorId", OPERATOR_ID)
        else:
            params.setdefault("operatorId", OPERATOR_ID)

    qs = _sorted_qs(params)
    url = BASE + endpoint + (("?" + qs) if qs else "")

    body_json = json.dumps(body_o, separators=(",", ":")) if body_o else None
    payload   = ts + method + endpoint + (("?" + qs) if qs else "") + (body_json or "")

    headers = {"Content-Type":"application/json"}
    if auth:
        sig = hmac.new(sec.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers.update({
            "Bitvavo-Access-Key": key,
            "Bitvavo-Access-Signature": sig,
            "Bitvavo-Access-Timestamp": ts,
            "Bitvavo-Access-Window": "10000",
        })

    data = body_json.encode() if body_json is not None else None
    req  = urllib.request.Request(url, data=data, headers=headers, method=method)
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

# ---------- selection ----------
def pick_pair():
    # AI hook
    if os.getenv("AI_PAIR","0") == "1":
        ai_sel = "/srv/trading/ai/ai_pair_selector.py"
        if os.path.isfile(ai_sel):
            try:
                out = subprocess.check_output([sys.executable, ai_sel], timeout=8).decode().strip()
                out = out.replace(",", " ").replace("["," ").replace("]"," ")
                for token in out.split():
                    tok = token.strip().upper().replace("_","-")
                    if tok.endswith("-EUR") and "BTC" not in tok:
                        return tok
            except Exception:
                pass
    # Fallback: bestaande pair_selector.py
    sel = "/srv/trading/tradingbot/tools/pair_selector.py"
    deny = {s.strip().upper() for s in os.getenv("PAIRSEL_DENY_BASES","BTC,ETH,BNB,ADA,SOL,XRP,USDT,USDC,EUR,USD,DAI").split(",") if s.strip()}
    if os.path.isfile(sel):
        try:
            p = subprocess.Popen([sys.executable, sel], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            start = time.time()
            chosen = None
            while time.time() - start < 12:
                line = p.stdout.readline()
                if not line: break
                m = re.search(r"selected\s*=\s*\[(.+?)\]", line, re.IGNORECASE)
                if m:
                    tokens = [t.strip().strip("\"' ").upper() for t in m.group(1).split(",")]
                    for t in tokens:
                        if t.endswith("-EUR") and "BTC" not in t:
                            base = t.split("-")[0]
                            if base not in deny:
                                chosen = t; break
                if chosen:
                    try: p.terminate(); p.wait(timeout=2)
                    except Exception: pass
                    return chosen
            try: p.terminate(); p.wait(timeout=2)
            except Exception: pass
        except Exception:
            pass
    # Fallback lijst
    for t in ["ICP-EUR","COTI-EUR","AAVE-EUR","ATOM-EUR","ALGO-EUR","FTM-EUR","NEAR-EUR","AR-EUR","RNDR-EUR"]:
        base=t.split("-")[0]
        if base != "BTC" and base not in deny:
            return t
    return None

# ---------- util ----------
def qdown(val, dec):
    q = decimal.Decimal(10) ** -dec
    return str(decimal.Decimal(val).quantize(q, rounding=decimal.ROUND_DOWN))

# ---------- main (validate-run: place + cancel) ----------
if __name__ == "__main__":
    # env laden
    for k,v in load_env_file("/srv/trading/.env.bitvavo").items():
        os.environ.setdefault(k,v)
    for k,v in load_env_file("/srv/trading/.env.trade").items():
        os.environ.setdefault(k,v)

    if not os.getenv("BITVAVO_API_KEY") or not os.getenv("BITVAVO_API_SECRET"):
        print("[error] missing API keys in .env.bitvavo"); sys.exit(1)

    TP_PCT = getenv_num("TP_PCT", 1.0)
    SL_PCT = getenv_num("SL_PCT", 0.6)
    MAX_EUR = getenv_num("MAX_NOTIONAL_EUR", 10.0)
    POST_ONLY = getenv_int("POST_ONLY", 1) == 1

    st_pub, r_pub = http("GET","/v2/time", auth=False)
    print("[public:/time]", st_pub, r_pub)

    market = pick_pair()
    if not market:
        print("[selected] NONE"); sys.exit(1)
    print("[selected]", market)

    st_mk, info = http("GET","/v2/markets", params={"market":market}, auth=False)
    if st_mk != 200 or not info:
        print("[market-info]", st_mk, info); sys.exit(1)
    mi = info[0] if isinstance(info, list) else info
    prec = mi.get("precision", {}) or {}
    pp = int(mi.get("pricePrecision") or prec.get("price", 2))
    ap = int(mi.get("amountPrecision") or prec.get("amount", 6))
    minQ = float(mi.get("minOrderInQuote", 0) or 0)
    minB = float(mi.get("minOrderInBase", 0) or 0)
    print(f"[market-info] pp={pp} ap={ap} minQuote={minQ} minBase={minB}")
    decimal.getcontext().prec = 28

    # validatierun: extreem lage prijs, zodat geen fill; amount uit MAX_EUR
    price = max(0.5, 10**(-pp))
    amount = max(minB, MAX_EUR / price if price>0 else MAX_EUR)
    price_s  = qdown(price, pp)
    amount_s = qdown(amount, ap)

    print("[targets]", {"tp_pct": TP_PCT, "sl_pct": SL_PCT})

    order = {
        "market": market,
        "side": "buy",
        "orderType": "limit",
        "amount": amount_s,
        "price": price_s,
        "timeInForce": "GTC",
        "postOnly": POST_ONLY
    }
    print("[debug:body]", json.dumps(order, separators=(",",":")))

    st_pl, r_pl = http("POST","/v2/order", body=order, auth=True)
    print("[place]", st_pl, r_pl)

    # direct annuleren (validatie)
    if st_pl == 200 and isinstance(r_pl, dict) and r_pl.get("orderId"):
        oid = r_pl["orderId"]
        st_ca, r_ca = http("DELETE","/v2/order", params={"market":market,"orderId":oid}, auth=True)
        print("[cancel]", st_ca, r_ca)
    else:
        print("[cancel] skipped (no orderId)")