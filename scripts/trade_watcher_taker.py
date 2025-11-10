#!/usr/bin/env python3
"""
trade_watcher_taker.py — Stap 9.6 (taker-variant, directe fill + TP/SL-bewaking)
- Leest instellingen uit /srv/trading/.env.trading
- Paar via AI hook (/srv/trading/ai/ai_pair_selector.py) als AI_PAIR=1, anders tools/pair_selector.py
- KOOPT als taker (market buy) ~MAX_NOTIONAL_EUR op de actuele ask (directe fill)
- Na fill: zet TP (limit sell) en bewaakt SL (market sell)
- OperatorId 1702 correct vóór signen (POST=body, GET/DELETE=query)
"""

import os, sys, time, json, hmac, hashlib, decimal, subprocess, re
import urllib.request, urllib.error

BASE = "https://api.bitvavo.com"
OPERATOR_ID = 1702

# ---------- env ----------
def load_env_file(path):
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, "r") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k,v=line.split("=",1)
            env[k.strip()]=v.strip().strip('"').strip("'")
    return env

def getenv_num(name, default):
    try: return float(os.getenv(name, str(default)).strip())
    except Exception: return default

def getenv_int(name, default):
    try: return int(os.getenv(name, str(default)).strip())
    except Exception: return default

# ---------- http ----------
def _qs(p):
    if not p: return ""
    return "&".join(f"{k}={v}" for k,v in sorted(p.items()))

def http(method, endpoint, params=None, body=None, auth=True, timeout=15):
    key=os.getenv("BITVAVO_API_KEY"); sec=os.getenv("BITVAVO_API_SECRET")
    ts=str(int(time.time()*1000))
    params=dict(params or {}); body_o=dict(body or {})

    if auth:
        if method in ("POST","PUT"): body_o.setdefault("operatorId", OPERATOR_ID)
        else: params.setdefault("operatorId", OPERATOR_ID)

    qs=_qs(params); url=BASE+endpoint+(("?"+qs) if qs else "")
    body_json=json.dumps(body_o, separators=(",",":")) if body_o else None
    payload=ts+method+endpoint+(("?"+qs) if qs else "")+(body_json or "")
    headers={"Content-Type":"application/json"}
    if auth:
        sig=hmac.new(sec.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers.update({
            "Bitvavo-Access-Key":key,
            "Bitvavo-Access-Signature":sig,
            "Bitvavo-Access-Timestamp":ts,
            "Bitvavo-Access-Window":"10000",
        })
    req=urllib.request.Request(url, data=(body_json.encode() if body_json else None),
                               headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {"error": e.reason}
    except Exception as e:
        return 0, {"error": str(e)}

# ---------- selection ----------
def pick_pair():
    # AI hook
    if os.getenv("AI_PAIR","0") == "1":
        ai = "/srv/trading/ai/ai_pair_selector.py"
        if os.path.isfile(ai):
            try:
                out=subprocess.check_output([sys.executable, ai], timeout=8).decode().strip()
                out=out.replace(","," ").replace("["," ").replace("]"," ")
                for tok in out.split():
                    t=tok.strip().upper().replace("_","-")
                    if t.endswith("-EUR") and "BTC" not in t:
                        return t
            except Exception:
                pass
    # Fallback
    sel="/srv/trading/tradingbot/tools/pair_selector.py"
    deny={s.strip().upper() for s in os.getenv("PAIRSEL_DENY_BASES","BTC,ETH,BNB,ADA,SOL,XRP,USDT,USDC,EUR,USD,DAI").split(",") if s.strip()}
    if os.path.isfile(sel):
        try:
            p=subprocess.Popen([sys.executable, sel], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            start=time.time(); chosen=None
            while time.time()-start<12:
                line=p.stdout.readline()
                if not line: break
                m=re.search(r"selected\s*=\s*\[(.+?)\]", line, re.IGNORECASE)
                if m:
                    toks=[t.strip().strip("\"' ").upper() for t in m.group(1).split(",")]
                    for t in toks:
                        if t.endswith("-EUR") and "BTC" not in t and t.split("-")[0] not in deny:
                            chosen=t; break
                if chosen:
                    try: p.terminate(); p.wait(timeout=2)
                    except Exception: pass
                    return chosen
            try: p.terminate(); p.wait(timeout=2)
            except Exception: pass
        except Exception:
            pass
    for t in ["ICP-EUR","COTI-EUR","AAVE-EUR","ATOM-EUR","ALGO-EUR","FTM-EUR","NEAR-EUR","AR-EUR","RNDR-EUR"]:
        if t.split("-")[0] not in deny: return t
    return None

# ---------- utils ----------
def qdown(val, dec):
    q=decimal.Decimal(10) ** -dec
    return str(decimal.Decimal(val).quantize(q, rounding=decimal.ROUND_DOWN))

def best_bid_ask(market):
    st, ob = http("GET","/v2/ticker/book", params={"market":market}, auth=False)
    if st==200 and isinstance(ob, dict) and ob.get("bid") and ob.get("ask"):
        return float(ob["bid"]), float(ob["ask"])
    st2, ob2 = http("GET","/v2/book", params={"market":market,"depth":5}, auth=False)
    if st2==200 and isinstance(ob2, dict) and ob2.get("bids") and ob2.get("asks"):
        return float(ob2["bids"][0][0]), float(ob2["asks"][0][0])
    return None, None

def parse_decimals_hint(msg):
    m = re.search(r"with\s+(\d+)\s+decimal digits", msg or "")
    return int(m.group(1)) if m else None

# ---------- main ----------
if __name__ == "__main__":
    # env
    for k,v in load_env_file("/srv/trading/.env.bitvavo").items(): os.environ.setdefault(k,v)
    for k,v in load_env_file("/srv/trading/.env.trading").items(): os.environ.setdefault(k,v)
    decimal.getcontext().prec=28

    TP_PCT = getenv_num("TP_PCT", 1.0)
    SL_PCT = getenv_num("SL_PCT", 0.6)
    MAX_EUR= getenv_num("MAX_NOTIONAL_EUR", 10.0)
    POLL   = getenv_int("POLL_SEC", 5)
    EXPIRE = getenv_int("EXPIRE_SEC", 900)

    st_pub, r_pub = http("GET","/v2/time", auth=False)
    print("[public:/time]", st_pub, r_pub)

    market = pick_pair()
    if not market:
        print("[selected] NONE"); sys.exit(1)
    print("[selected]", market)

    st_mk, info = http("GET","/v2/markets", params={"market":market}, auth=False)
    if st_mk!=200 or not info:
        print("[market-info]", st_mk, info); sys.exit(1)
    mi = info[0] if isinstance(info, list) else info
    prec = mi.get("precision", {}) or {}
    pp = int(mi.get("pricePrecision") or prec.get("price", 2))
    ap = int(mi.get("amountPrecision") or prec.get("amount", 6))
    minB = float(mi.get("minOrderInBase", 0) or 0)
    print(f"[market-info] pp={pp} ap={ap}")

    bid, ask = best_bid_ask(market)
    if bid is None or ask is None:
        print("[orderbook] unavailable", market); sys.exit(0)
    print("[best-bbo]", {"bid": bid, "ask": ask})

    # taker: market BUY voor ~MAX_EUR notional
    amount_raw = MAX_EUR / ask if ask>0 else MAX_EUR
    if minB and amount_raw < minB: amount_raw = minB

    def place_market_buy(decimals):
        amt = qdown(amount_raw, decimals)
        body={"market":market,"side":"buy","orderType":"market","amount":amt}
        print("[buy-intent]", {"amount": amt, "amt_decimals": decimals})
        return http("POST","/v2/order", body=body, auth=True)

    st_pl, r_pl = place_market_buy(ap)
    if st_pl==400 and isinstance(r_pl, dict) and "error" in r_pl:
        hint = parse_decimals_hint(r_pl["error"])
        tried=set([ap])
        if hint is not None and hint not in tried:
            st_pl, r_pl = place_market_buy(hint); tried.add(hint)
        dec = ap-1
        while st_pl!=200 and dec>=0:
            if dec not in tried:
                st_pl, r_pl = place_market_buy(dec); tried.add(dec)
            dec-=1

    print("[buy-place]", st_pl, r_pl)
    if st_pl!=200 or "orderId" not in r_pl:
        print("[exit] failed to place buy"); sys.exit(0)
    buy_oid = r_pl["orderId"]

    # vrijwel direct filled bij market; bevestig prijs
    filled_price=None; amount_filled=None
    started=time.time()
    while time.time()-started < 30:
        st_q, q = http("GET","/v2/order", params={"market":market,"orderId":buy_oid}, auth=True)
        if st_q==200 and isinstance(q, dict):
            fa = float(q.get("filledAmount","0") or 0)
            fq = float(q.get("filledAmountQuote","0") or 0)
            if fa>0:
                amount_filled = fa
                filled_price = (fq/fa) if fa>0 else ask
                break
            if q.get("status") in ("canceled","rejected"):
                print("[exit] buy canceled/rejected"); sys.exit(0)
        time.sleep(2)

    if filled_price is None:
        print("[exit] unexpected: no fill on market"); sys.exit(0)
    print("[buy-filled]", {"price": round(filled_price, pp), "amount": amount_filled})

    # TP/SL niveaus
    tp_price = filled_price * (1 + TP_PCT/100.0)
    sl_price = filled_price * (1 - SL_PCT/100.0)
    tp_s = qdown(tp_price, pp)
    amt_s = qdown(amount_filled, ap)
    print("[targets]", {"tp_pct": TP_PCT, "sl_pct": SL_PCT, "tp_price": tp_s, "sl_price": round(sl_price, pp)})

    # TP-limit SELL
    st_tp, r_tp = http("POST","/v2/order",
                       body={"market":market,"side":"sell","orderType":"limit",
                             "amount":amt_s,"price":tp_s,
                             "timeInForce":"GTC","postOnly":False},
                       auth=True)
    print("[tp-place]", st_tp, r_tp)
    tp_oid = r_tp.get("orderId") if st_tp==200 else None

    # Bewaken tot TP of SL of expiry
    start=time.time()
    while time.time()-start < EXPIRE:
        st_t, t = http("GET","/v2/ticker/price", params={"market":market}, auth=False)
        if st_t==200 and isinstance(t, dict) and t.get("price"):
            px=float(t["price"])
            if px <= sl_price:
                print("[signal] SL", px)
                if tp_oid:
                    http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
                st_m, r_m = http("POST","/v2/order",
                                 body={"market":market,"side":"sell","orderType":"market","amount":amt_s},
                                 auth=True)
                print("[sl-sell]", st_m, r_m)
                sys.exit(0)
        if tp_oid:
            stq, oq = http("GET","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
            if stq==200 and isinstance(oq, dict) and oq.get("status") in ("filled","canceled"):
                print("[tp-status]", oq.get("status")); sys.exit(0)
        time.sleep(POLL)
    # failsafe
    if tp_oid:
        http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
    st_f, r_f = http("POST","/v2/order",
                     body={"market":market,"side":"sell","orderType":"market","amount":amt_s},
                     auth=True)
    print("[failsafe-exit]", st_f, r_f)