#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, time, decimal, logging
from typing import Dict, Any, List, Tuple
from redis import Redis
from redis.exceptions import ResponseError
from python_bitvavo_api.bitvavo import Bitvavo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
D = decimal.Decimal

REDIS_URL    = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
ORDER_STREAM = os.getenv("ORDER_STREAM", "orders:live")
EXEC_STREAM  = os.getenv("EXEC_STREAM",  "orders:executed")
GROUP        = os.getenv("GROUP",        "trading_submitter")
CONSUMER     = os.getenv("CONSUMER",     "submitter-1")
DRY          = os.getenv("DRY", "0") in ("1","true","TRUE","yes","YES")

APIKEY   = os.getenv("BITVAVO_API_KEY", "")
APISECRET= os.getenv("BITVAVO_API_SECRET", "")
OPID     = os.getenv("BITVAVO_OPERATOR_ID", "")

def ensure_group(r: Redis):
    try:
        r.xgroup_create(name=ORDER_STREAM, groupname=GROUP, id="$", mkstream=True)
        logging.info("XGROUP CREATE %s %s", ORDER_STREAM, GROUP)
    except ResponseError as e:
        if "BUSYGROUP" in str(e) or "exists" in str(e):
            logging.info("XGROUP OK %s %s", ORDER_STREAM, GROUP)
        else:
            raise

def emit_executed(r: Redis, mid: str, status: str, obj: Any):
    r.xadd(EXEC_STREAM, {"status": status, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "mid": mid, "order": json.dumps(obj, separators=(",",":"))}, id="*")

def parse_payload(fields: Dict[str,str]) -> Dict[str,Any]:
    if "data" in fields:
        try:
            return json.loads(fields["data"])
        except Exception:
            logging.warning("Kon 'data' niet parsen; val terug op ruwe velden")
    p = {k: fields.get(k) for k in ("market","side","orderType","amount","price","mode","tp_pct","sl_pct","trail_pct","src")}
    if not p.get("orderType"): p["orderType"] = "market"
    return p

def build_body(p: Dict[str,Any]) -> Dict[str,Any]:
    body = {
        "market": p.get("market"),
        "side":   p.get("side"),
        "orderType": p.get("orderType","market"),
    }
    if body["orderType"] == "limit":
        body["price"] = p.get("price")
    amt = p.get("amount")
    if amt is not None:
        body["amount"] = str(amt)
    return body

def place_order_live(bv: Bitvavo, req: Dict[str,Any]) -> Dict[str,Any]:
    body = {k:v for k,v in req.items() if v is not None}
    if OPID:
        body["operatorId"] = OPID
    resp = bv.placeOrder(body)
    return {"request": body, "response": resp}

def main():
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    ensure_group(r)
    live = not DRY
    bv = None
    if live:
        if not APIKEY or not APISECRET:
            logging.error("Geen Bitvavo API creds in env; stop.")
            return
        bv = Bitvavo({"APIKEY": APIKEY, "APISECRET": APISECRET})

    logging.info("Submitter gestart | stream=%s group=%s consumer=%s live=%s",
                 ORDER_STREAM, GROUP, "submitter-1", str(live))

    while True:
        try:
            resp = r.xreadgroup(GROUP, "submitter-1", {ORDER_STREAM: ">"}, count=10, block=10000)
            if not resp:
                continue
            items: List[Tuple[str, Dict[str,str]]] = resp[0][1]
            for mid, fields in items:
                try:
                    p = parse_payload(fields)
                    if not p.get("market") or not p.get("side"):
                        raise ValueError("payload mist market/side")
                    req = build_body(p)
                    if live:
                        result = place_order_live(bv, req)
                        emit_executed(r, mid, "LIVE_OK", result)
                    else:
                        fake = {**req, "src": p.get("src","core_outbox_v2")}
                        emit_executed(r, mid, "DRY_OK", fake)
                    r.xack(ORDER_STREAM, GROUP, mid)
                except Exception as e:
                    logging.error("ORDER EXC id=%s payload=%s err=%s",
                                  mid, json.dumps(fields)[:500], repr(e))
        except Exception as outer:
            logging.error("LOOP EXC %s", repr(outer))
            time.sleep(1)

if __name__ == "__main__":
    main()
