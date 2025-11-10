#!/usr/bin/env python3
"""
trade_watcher_final.py — TP/SL watcher met taker-fallback + precisiecache

Fixes:
- TP lager dan SL: beide prijzen nu met dezelfde truncatie (qdown) i.p.v. mix van round()/qdown().
- Minder “decimal digits”-errors: onthoud per market het geaccepteerde aantal amount-decimals in
  /srv/trading/storage/precision_cache.json en gebruik dat direct bij volgende orders.
"""

import os, sys, time, json, hmac, hashlib, decimal, subprocess, re
import urllib.request, urllib.error

BASE = "https://api.bitvavo.com"
OPERATOR_ID = 1702
PRECISION_CACHE = "/srv/trading/storage/precision_cache.json"

# ------------- env -------------
def load_env_file(path):
    env = {}
    if not os.path.isfile(path): return env
    with open(path, "r") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    return env

def getenv_num(name, default):
    try: return float(os.getenv(name, str(default)).strip())
    except Exception: return default

def getenv_int(name, default):
    try: return int(os.getenv(name, str(default)).strip())
    except Exception: return default

def getenv_str(name, default):
    v=os.getenv(name,"").strip(); return v if v else default

# ------------- http -------------
def _sorted_qs(params):
    if not params: return ""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))

def http(method, endpoint, params=None, body=None, auth=True, timeout=15):
    key=os.getenv("BITVAVO_API_KEY"); sec=os.getenv("BITVAVO_API_SECRET")
    ts=str(int(time.time()*1000))
    params=dict(params or {}); body_o=dict(body or {})

    if auth:
        if method in ("POST","PUT"):
            body_o.setdefault("operatorId", OPERATOR_ID)
        else:
            params.setdefault("operatorId", OPERATOR_ID)

    qs=_sorted_qs(params); url=BASE+endpoint+(("?"+qs) if qs else "")
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

# ------------- selection -------------
def pick_pair():
    if os.getenv("AI_PAIR","0") == "1":
        ai="/srv/trading/ai/ai_pair_selector.py"
        if os.path.isfile(ai):
            try:
                out=subprocess.check_output([sys.executable, ai], timeout=8).decode().strip()
                out=out.replace(","," ").replace("["," ").replace("]"," ")
                for tok in out.split():
                    t=tok.strip().upper().replace("_","-")
                    if t.endswith("-EUR") and "BTC" not in t: return t
            except Exception: pass
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
        except Exception: pass
    for t in ["ICP-EUR","COTI-EUR","AAVE-EUR","ATOM-EUR","ALGO-EUR","FTM-EUR","NEAR-EUR","AR-EUR","RNDR-EUR"]:
        if t.split("-")[0] not in deny: return t
    return None

# ------------- utils -------------
def qdown(val, dec):
    q=decimal.Decimal(10) ** -dec
    return str(decimal.Decimal(val).quantize(q, rounding=decimal.ROUND_DOWN))

def best_bid_ask(market):
    st_tb, tb = http("GET","/v2/ticker/book", params={"market":market}, auth=False)
    if st_tb==200 and isinstance(tb, dict) and tb.get("bid") and tb.get("ask"):
        return float(tb["bid"]), float(tb["ask"])
    st_ob, ob = http("GET","/v2/book", params={"market":market,"depth":5}, auth=False)
    if st_ob==200 and isinstance(ob, dict) and ob.get("bids") and ob.get("asks"):
        return float(ob["bids"][0][0]), float(ob["asks"][0][0])
    return None, None

def parse_decimals_hint(msg):
    m=re.search(r"with\s+(\d+)\s+decimal digits", (msg or ""))
    return int(m.group(1)) if m else None

# ------------- precision cache -------------
def load_cache():
    try:
        if os.path.isfile(PRECISION_CACHE):
            with open(PRECISION_CACHE,"r") as f: return json.load(f)
    except Exception: pass
    return {}

def save_cache(cache):
    try:
        os.makedirs(os.path.dirname(PRECISION_CACHE), exist_ok=True)
        tmp=PRECISION_CACHE+".tmp"
        with open(tmp,"w") as f: json.dump(cache,f)
        os.replace(tmp, PRECISION_CACHE)
    except Exception: pass

def used_decimals_from_amount_str(s):
    if not isinstance(s,str) or "." not in s: return 0
    return max(0, len(s.split(".",1)[1].rstrip("0")))

# ------------- core helpers -------------
def place_with_decimal_fallback(kind, market, body_base, start_decimals):
    """
    Plaatst order met amount-truncatie; gebruikt hint uit fouttekst of probeert dec-1..0.
    Logt elke poging in [kind-attempt].
    """
    tried=set()
    def _try(decimals):
        tried.add(decimals)
        body=dict(body_base)
        if "amount" in body:
            body["amount"]=qdown(decimal.Decimal(body["amount"]), decimals)
        st, resp = http("POST","/v2/order", body=body, auth=True)
        print(f"[{kind}-attempt]", {"decimals": decimals, "status": st})
        return st, resp

    st, resp = _try(start_decimals)
    if st==400 and isinstance(resp, dict) and "error" in resp and "decimal" in resp["error"].lower():
        hint = parse_decimals_hint(resp["error"])
        if hint is not None and hint not in tried:
            st, resp = _try(hint)
        dec = start_decimals-1
        while st!=200 and dec>=0:
            if dec not in tried:
                st, resp = _try(dec)
            dec -= 1
    return st, resp

def entry_and_manage(market, pp, ap, minB, entry_mode, post_only, max_eur, tp_pct, sl_pct,
                     fill_wait_sec, tp_fallback_sec, poll_sec, expire_sec):
    cache = load_cache()
    start_ap = int(cache.get(market, ap))

    bid, ask = best_bid_ask(market)
    if bid is None or ask is None:
        print("[orderbook] unavailable", market); return

    decimal.getcontext().prec = 28

    # === ENTRY ===
    buy_oid=None; filled_price=None; filled_amount=None; used_ap=start_ap

    if entry_mode == "taker":
        amount_raw = max_eur / ask if ask>0 else max_eur
        if minB and amount_raw < minB: amount_raw = minB
        body={"market":market,"side":"buy","orderType":"market","amount":str(amount_raw)}
        st_pl, r_pl = place_with_decimal_fallback("buy-market", market, body, start_ap)
        print("[buy-place]", st_pl, r_pl)
        if st_pl!=200 or "orderId" not in r_pl:
            print("[exit] failed to place buy"); return
        buy_oid = r_pl["orderId"]
        # direct fills of korte confirm
        fa = float(r_pl.get("filledAmount","0") or 0)
        fq = float(r_pl.get("filledAmountQuote","0") or 0)
        if fa>0:
            filled_amount = fa
            filled_price  = (fq/fa) if fa>0 else ask
        else:
            start=time.time()
            while filled_price is None and time.time()-start<30:
                st_q, q = http("GET","/v2/order", params={"market":market,"orderId":buy_oid}, auth=True)
                if st_q==200 and isinstance(q, dict):
                    fa = float(q.get("filledAmount","0") or 0); fq = float(q.get("filledAmountQuote","0") or 0)
                    if fa>0: filled_amount=fa; filled_price=(fq/fa) if fa>0 else ask; break
                    if q.get("status") in ("canceled","rejected"):
                        print("[exit] buy canceled/rejected"); return
                time.sleep(2)
        if filled_price is None:
            print("[exit] unexpected: no fill on market (taker)"); return

        used_ap = used_decimals_from_amount_str(r_pl.get("amount","")) or start_ap

    else:
        entry_price = bid * 1.0005
        amount_raw = max_eur / entry_price if entry_price>0 else max_eur
        if minB and amount_raw < minB: amount_raw = minB
        body={"market":market,"side":"buy","orderType":"limit",
              "amount":str(amount_raw),"price":qdown(entry_price, pp),
              "timeInForce":"GTC","postOnly":post_only}
        st_pl, r_pl = place_with_decimal_fallback("buy-limit", market, body, start_ap)
        print("[buy-place]", st_pl, r_pl)
        if st_pl!=200 or "orderId" not in r_pl:
            print("[exit] failed to place buy"); return
        buy_oid = r_pl["orderId"]
        start=time.time()
        while time.time()-start < fill_wait_sec:
            st_q, q = http("GET","/v2/order", params={"market":market,"orderId":buy_oid}, auth=True)
            if st_q==200 and isinstance(q, dict):
                status=q.get("status",""); fa=float(q.get("filledAmount","0") or 0); fq=float(q.get("filledAmountQuote","0") or 0)
                if status in ("filled","partiallyFilled") and fa>0:
                    filled_amount=fa; filled_price=(fq/fa) if fa>0 else float(q.get("price") or entry_price); break
                if status in ("canceled","rejected"):
                    print("[exit] buy not filled:", status); return
            time.sleep(poll_sec)
        if filled_price is None:
            http("DELETE","/v2/order", params={"market":market,"orderId":buy_oid}, auth=True)
            print("[exit] buy timeout -> canceled"); return

        used_ap = used_decimals_from_amount_str(r_pl.get("amount","")) or start_ap

    # cache bijwerken
    cache[market] = int(used_ap)
    save_cache(cache)

    # logging met consistente qdown
    print("[buy-filled]", {"price": qdown(filled_price, pp), "amount": qdown(filled_amount, used_ap)})

    # === EXIT MANAGEMENT ===
    tp_price = filled_price * (1 + tp_pct/100.0)
    sl_price = filled_price * (1 - sl_pct/100.0)
    tp_s = qdown(tp_price, pp)
    sl_s = qdown(sl_price, pp)   # alleen voor weergave; trigger blijft op echte sl_price

    exit_amt = qdown(filled_amount, used_ap)

    # TP (limit) met decimal-fallback
    tp_body={"market":market,"side":"sell","orderType":"limit",
             "amount":exit_amt,"price":tp_s,"timeInForce":"GTC","postOnly":False}
    st_tp, r_tp = place_with_decimal_fallback("sell-limit", market, tp_body, start_decimals=int(used_ap))
    print("[targets]", {"tp_pct": tp_pct, "sl_pct": sl_pct, "tp_price": tp_s, "sl_price": sl_s})
    print("[tp-place]", st_tp, r_tp)
    tp_oid = r_tp.get("orderId") if st_tp==200 else None

    # bewaak tot TP/SL/timeout
    started=time.time(); tp_started=time.time()
    while time.time()-started < expire_sec:
        # SL
        st_t, t = http("GET","/v2/ticker/price", params={"market":market}, auth=False)
        if st_t==200 and isinstance(t, dict) and t.get("price"):
            px=float(t["price"])
            if px <= sl_price:
                print("[signal] SL", px)
                if tp_oid:
                    http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
                m_body={"market":market,"side":"sell","orderType":"market","amount":exit_amt}
                st_m, r_m = place_with_decimal_fallback("sell-market", market, m_body, start_decimals=int(used_ap))
                print("[sl-sell]", st_m, r_m)
                return

        # TP status
        if tp_oid:
            stq, oq = http("GET","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
            if stq==200 and isinstance(oq, dict) and oq.get("status") in ("filled","canceled"):
                print("[tp-status]", oq.get("status")); return

        # TP → taker fallback na timeout
        if tp_oid and (time.time()-tp_started >= tp_fallback_sec):
            print("[tp-fallback] timeout reached -> taker exit")
            http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
            m_body={"market":market,"side":"sell","orderType":"market","amount":exit_amt}
            st_m, r_m = place_with_decimal_fallback("sell-market", market, m_body, start_decimals=int(used_ap))
            print("[tp-fallback-sell]", st_m, r_m)
            return

        time.sleep(poll_sec)

    # failsafe
    if tp_oid:
        http("DELETE","/v2/order", params={"market":market,"orderId":tp_oid}, auth=True)
    m_body={"market":market,"side":"sell","orderType":"market","amount":exit_amt}
    st_f, r_f = place_with_decimal_fallback("sell-market", market, m_body, start_decimals=int(used_ap))
    print("[failsafe-exit]", st_f, r_f)

# ------------- main -------------
if __name__ == "__main__":
    for k,v in load_env_file("/srv/trading/.env.bitvavo").items(): os.environ.setdefault(k,v)
    for k,v in load_env_file("/srv/trading/.env.trading").items(): os.environ.setdefault(k,v)

    TP_PCT   = getenv_num("TP_PCT", 1.0)
    SL_PCT   = getenv_num("SL_PCT", 0.6)
    MAX_EUR  = getenv_num("MAX_NOTIONAL_EUR", 10.0)
    ENTRY_MODE = getenv_str("ENTRY_MODE", "taker").lower()  # taker|maker
    POST_ONLY  = getenv_int("POST_ONLY", 1) == 1
    FILL_WAIT  = getenv_int("FILL_WAIT_SEC", 180)
    TP_FALLBACK_SEC = getenv_int("TP_FALLBACK_SEC", 300)
    POLL_SEC   = getenv_int("POLL_SEC", 5)
    EXPIRE_SEC = getenv_int("EXPIRE_SEC", 900)
    MANAGE_H   = getenv_int("MANAGE_HOLDINGS", 0) == 1

    decimal.getcontext().prec = 28

    st_pub, r_pub = http("GET","/v2/time", auth=False)
    print("[public:/time]", st_pub, r_pub)

    if MANAGE_H:
        print("[holdings] route not used in this build"); sys.exit(0)

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
    minB = float(mi.get("minOrderInBase", 0) or 0)
    print(f"[market-info] pp={pp} ap={ap}")

    entry_and_manage(market, pp, ap, minB, ENTRY_MODE, POST_ONLY, MAX_EUR,
                     TP_PCT, SL_PCT, FILL_WAIT, TP_FALLBACK_SEC, POLL_SEC, EXPIRE_SEC)