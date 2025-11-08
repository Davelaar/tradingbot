#!/usr/bin/env python3
import os, sys, time, json, logging
from typing import Dict, Any
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
ORDER_STREAM = os.getenv("ORDER_OUTBOX_STREAM", "orders:live")
GROUP = os.getenv("CONSUMER_GROUP", "trading_submitter")
CONSUMER = os.getenv("CONSUMER_NAME", "submitter-1")
ENABLE_LIVE = os.getenv("ENABLE_LIVE", "false").lower() == "true"

BITVAVO_API_KEY = os.getenv("BITVAVO_API_KEY")
BITVAVO_API_SECRET = os.getenv("BITVAVO_API_SECRET")
BITVAVO_REST_URL = os.getenv("BITVAVO_REST_URL", "https://api.bitvavo.com/v2")
BITVAVO_WS_URL = os.getenv("BITVAVO_WS_URL", "wss://ws.bitvavo.com/v2")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("submitter")

try:
    from python_bitvavo_api.bitvavo import Bitvavo
except Exception as e:
    log.error("python_bitvavo_api niet geïnstalleerd: %s", e)
    sys.exit(1)

def ensure_group(r: redis.Redis):
    try:
        r.xgroup_create(name=ORDER_STREAM, groupname=GROUP, id="$", mkstream=True)
        log.info("XGROUP CREATE %s %s", ORDER_STREAM, GROUP)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            pass
        else:
            raise

def parse_order(fields: Dict[str, Any]) -> Dict[str, Any]:
    f = {k: str(v) for k, v in fields.items()}
    need = ("market", "side", "orderType", "amount")
    for n in need:
        if n not in f:
            raise ValueError(f"Onvolledige order; mist veld '{n}'")
    order = {
        "market": f["market"],
        "side": f["side"],
        "orderType": f["orderType"],
    }
    if order["orderType"].lower() == "market":
        if order["market"].endswith("-EUR"):
            order["amountQuote"] = f["amount"]
        else:
            order["amount"] = f["amount"]
    else:
        order["amount"] = f["amount"]
        if "price" in f:
            order["price"] = f["price"]
    return order

def make_client():
    if ENABLE_LIVE and (not BITVAVO_API_KEY or not BITVAVO_API_SECRET):
        raise RuntimeError("ENABLE_LIVE=true maar BITVAVO_API_KEY/SECRET ontbreken")
    from python_bitvavo_api.bitvavo import Bitvavo
    return Bitvavo({
        "APIKEY": BITVAVO_API_KEY,
        "APISECRET": BITVAVO_API_SECRET,
        "RESTURL": BITVAVO_REST_URL,
        "WSURL": BITVAVO_WS_URL,
        "ACCESSWINDOW": 10000
    })

def main():
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    ensure_group(r)
    bv = make_client() if ENABLE_LIVE else None
    log.info("Submitter gestart | stream=%s group=%s consumer=%s live=%s",
             ORDER_STREAM, GROUP, CONSUMER, ENABLE_LIVE)

    while True:
        resp = r.xreadgroup(GROUP, CONSUMER, {ORDER_STREAM: ">"}, count=10, block=5000)
        if not resp:
            continue
        for _stream, entries in resp:
            for msg_id, kv in entries:
                try:
                    order = parse_order(kv)
                    if ENABLE_LIVE:
                        res = bv.placeOrder(**order)
                        log.info("ORDER OK id=%s order=%s res=%s", msg_id, json.dumps(order), json.dumps(res))
                    else:
                        log.info("DRY-RUN id=%s order=%s", msg_id, json.dumps(order))
                    r.xack(ORDER_STREAM, GROUP, msg_id)
                except Exception as e:
                    log.error("ORDER FAIL id=%s err=%s payload=%s", msg_id, e, json.dumps(kv))
        time.sleep(0.05)

if __name__ == "__main__":
    main()
