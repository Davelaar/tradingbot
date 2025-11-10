#!/usr/bin/env python3
"""
trade_watcher.py — Stap 9.6 (bewaking met TP/SL)
- Leest TP/SL en instellingen uit /srv/trading/.env.trading
- Kiest paar via AI hook (/srv/trading/ai/ai_pair_selector.py) als AI_PAIR=1, anders via tools/pair_selector.py
- Plaatst een BUY-limit (maker-vriendelijk) rond de top van de orderbook-bid en wacht op fill (met timeout)
- Na fill: berekent TP/SL o.b.v. fillprijs en bewaakt:
    * TP: SELL-limit (kan direct fillen)
    * SL: SELL-market (nooduitgang), annuleert open TP-order
- OperatorId 1702 wordt correct vóór het signen toegevoegd (POST body / GET-DELETE query)
- FIX: als Bitvavo 429 “amount ... too many decimal digits” geeft, rond dan het amount automatisch verder af
       naar beneden totdat de order geaccepteerd wordt (detectie op basis van fouttekst).
"""

import os, sys, time, json, hmac, hashlib, decimal, subprocess, re
import urllib.request, urllib.error

BASE = "https://api.bitvavo.com"
OPERATOR_ID = 1702

# --------- env helpers ----------
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
    try:
        return float(os.getenv(name, str(default)).strip())
    except Exception:
        return default

def getenv_int(name, default):
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default

# --------- HTTP + signing ----------
def _sorted_qs(params):
    if not params: return ""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))

def http(method, endpoint, params=None, body=None, auth=True, timeout=15):
    key = os.getenv("BITVAVO_API_KEY")
    sec = os.getenv("BITVAVO_API_SECRET")
    ts  = str(int(time.time()*1000))

    params = dict(params or {})
    body_o = dict(body or {})

    # operatorId toevoegen vóór signen
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
        try:    return e.code, json.loads(e.read().decode())
        except: return e.code, {"error": e.reason}
    except Exception as e:
        return 0, {"error": str(e)}

# --------- selectie ----------
def pick_pair():
    # AI-hook
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
            start = time.time(); chosen = None
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

# --------- util ----------
def qdown(val, dec):
    q = decimal.Decimal(10) ** -dec
    return str(decimal.Decimal(val).quantize(q, rounding=decimal.ROUND_DOWN))

def get_best_bid_ask(market):
    """Probeer eerst /v2/book (depth=5). Als dat 404 geeft, val terug op /v2/ticker/book."""
    st_ob, ob = http("GET","/v2/book", params={"market":market, "depth":5}, auth=False)
    if st_ob == 200 and isinstance(ob, dict) and ob.get("bids") and ob.get("asks") and len(ob["bids"])>0 and len(ob["asks"])>0:
        return float(ob["bids"][0][0]), float(ob["asks"][0][0])
    st_tb, tb = http("GET","/v2/ticker/book", params={"market":market}, auth=False)
    if st_tb == 200 and isinstance(tb, dict) and tb.get("bid") and tb.get("ask"):
        return float(tb["bid"]), float(tb["ask"])
    return None, None

def parse_required_decimals_from_error(msg: str) -> int | None:
    """
    Zoek in Bitvavo-fouttekst naar een hint over decimalen, bv:
    "Examples of numbers with 2 decimal digits: ..." -> retourneer 2
    """
    m = re.search(r"with\s+(\d+)\s+decimal digits", msg)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None

# --------- hoofdprogramma ----------
if __name__ == "__main__":
    # env laden
    for k,v in load_env_file("/srv/trading/.env.bitvavo").items():
        os.environ.setdefault(k,v)
    for k,v in load_env_file("/srv/trading/.env.trading").items():
        os.environ.setdefault(k,v)

    # parameters
    TP_PCT     = getenv_num("TP_PCT", 1.0)      # bijv. 1.0 (%)
    SL_PCT     = getenv_num("SL_PCT", 0.6)      # bijv. 0.6 (%)
    MAX_EUR    = getenv_num("MAX_NOTIONAL_EUR", 10.0)
    POST_ONLY  = getenv_int("POST_ONLY", 1) == 1
    FILL_WAIT  = getenv_int("FILL_WAIT_SEC", 180)
    POLL_SEC   = getenv_int("POLL_SEC", 5)
    EXPIRE_SEC = getenv_int("EXPIRE_SEC", 900)

    # publieke check
    st_pub, r_pub = http("GET","/v2/time", auth=False)
    print("[public:/time]", st_pub, r_pub)

    market = pick_pair()
    if not market:
        print("[selected] NONE"); sys.exit(1)
    print("[selected]", market)

    # marktinfo
    st_mk, info = http("GET","/v2/markets", params={"market":market}, auth=False)
    if st_mk != 200 or not info:
        print("[market-info]", st_mk, info); sys.exit(1)
    mi = info[0] if isinstance(info, list) else info
    prec = mi.get("precision", {}) or {}
    pp = int(mi.get("pricePrecision") or prec.get("price", 2))
    ap = int(mi.get("amountPrecision") or prec.get("amount", 6))
    minB = float(mi.get("minOrderInBase", 0) or 0)
    print(f"[market-info] pp={pp} ap={ap}")

    # best bid/ask met fallback
    best_bid, best_ask = get_best_bid_ask(market)
    if best_bid is None or best_ask is None:
        print("[orderbook]", "unavailable for", market)
        sys.exit(0)
    print("[best-bbo]", {"bid": best_bid, "ask": best_ask})

    # kooporder als maker: 0.05% boven bid; afronden naar precisie
    decimal.getcontext().prec = 28
    entry_price = best_bid * 1.0005
    amount_raw = MAX_EUR / entry_price if entry_price > 0 else MAX_EUR
    if minB and amount_raw < minB:
        amount_raw = minB

    def place_buy_with_precision(decimals: int):
        price_s  = qdown(entry_price, pp)
        amount_s = qdown(amount_raw, decimals)
        buy = {
            "market": market, "side": "buy", "orderType": "limit",
            "amount": amount_s, "price": price_s,
            "timeInForce": "GTC", "postOnly": POST_ONLY
        }
        print("[buy-intent]", {"price": price_s, "amount": amount_s, "amt_decimals": decimals})
        return http("POST","/v2/order", body=buy, auth=True)

    # Eerste poging met ap, daarna auto-downgrade bij "too many decimal digits"
    st_pl, r_pl = place_buy_with_precision(ap)
    if st_pl == 400 and isinstance(r_pl, dict) and "error" in r_pl and "decimal" in r_pl["error"].lower():
        hinted = parse_required_decimals_from_error(r_pl["error"])
        # probeer: (hint) -> (ap-1 downto 0) zonder te gokken
        tried = set()
        if hinted is not None:
            tried.add(hinted)
            st_pl, r_pl = place_buy_with_precision(hinted)
        dec = ap - 1
        while (st_pl != 200) and dec >= 0:
            if dec not in tried:
                tried.add(dec)
                st_pl, r_pl = place_buy_with_precision(dec)
            dec -= 1

    print("[buy-place]", st_pl, r_pl)
    if st_pl != 200 or "orderId" not in r_pl:
        print("[exit] failed to place buy"); sys.exit(0)
    oid = r_pl["orderId"]

    # wachten op fill (of timeout)
    start = time.time()
    filled_price = None
    while time.time() - start < FILL_WAIT:
        st_q, q = http("GET","/v2/order", params={"market":market,"orderId":oid}, auth=True)
        if st_q == 200 and isinstance(q, dict):
            status = q.get("status","")
            fa = float(q.get("filledAmount","0") or 0)
            if status in ("filled","partiallyFilled") and fa > 0:
                filled_quote = float(q.get("filledAmountQuote","0") or 0)
                filled_price = filled_quote / fa if fa>0 else None
                break
            if status in ("canceled","rejected"):
                print("[exit] buy not filled:", status); sys.exit(0)
        time.sleep(POLL_SEC)

    # als niet gevuld: annuleer en stoppen
    if filled_price is None:
        http("DELETE","/v2/order", params={"market":market,"orderId":oid}, auth=True)
        print("[exit] buy timeout -> canceled"); sys.exit(0)

    print("[buy-filled]", {"price": round(filled_price, pp)})

    # TP/SL niveaus
    TP_PCT = float(TP_PCT); SL_PCT = float(SL_PCT)
    tp_price = filled_price * (1 + TP_PCT/100.0)
    sl_price = filled_price * (1 - SL_PCT/100.0)
    tp_s = qdown(tp_price, pp)
    print("[targets]", {"tp_pct": TP_PCT, "sl_pct": SL_PCT, "tp_price": tp_s, "sl_price": round(sl_price, pp)})

    # plaats TP-limit SELL, SL doen we met watcher + market sell
    amount_s_for_exit = qdown(amount_raw, ap)  # gebruik originele ap voor exit
    st_tp, r_tp = http("POST","/v2/order",
                       body={"market":market,"side":"sell","orderType":"limit",
                             "amount":amount_s_for_exit,"price":tp_s,
                             "timeInForce":"GTC","postOnly":False},
                       auth=True)
    print("[tp-place]", st_tp, r_tp)
    tp_oid = r_tp.get("orderId") if st_tp == 200 else None

    # bewaken tot TP of SL of expiry
    started = time.time()
    while time.time() - started < EXPIRE_SEC:
        st_t, t = http("GET","/v2/ticker/price", params={"market":market}, auth=False)
        if st_t == 200 and isinstance(t, dict) and t.get("price"):
            px = float(t["price"])
            if px <= sl_price:
                print("[signal] stoploss trigger @", px)
                if tp_oid:
                    http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
                st_m, r_m = http("POST","/v2/order",
                                 body={"market":market,"side":"sell","orderType":"market","amount":amount_s_for_exit},
                                 auth=True)
                print("[sl-sell]", st_m, r_m)
                sys.exit(0)

        if tp_oid:
            stq, oq = http("GET","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
            if stq == 200 and isinstance(oq, dict) and oq.get("status") in ("filled","canceled"):
                print("[tp-status]", oq.get("status"))
                sys.exit(0)

        time.sleep(POLL_SEC)

    # verloop-tijd bereikt → annuleer TP en sluit positie market (failsafe)
    if tp_oid:
        http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
    st_f, r_f = http("POST","/v2/order",
                     body={"market":market,"side":"sell","orderType":"market","amount":amount_s_for_exit},
                     auth=True)
    print("[failsafe-exit]", st_f, r_f)