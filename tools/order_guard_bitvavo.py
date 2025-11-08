#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live Exit Guard (TP/SL) for Bitvavo.

- Consumes Redis stream ORDER_EXEC_STREAM (default: "orders:executed")
  via consumer group CONSUMER_GROUP (default: trading_guard), consumer CONSUMER_NAME.
- Expects a Bitvavo order response in field "response".
- Calculates TP (limit sell) and SL (stopLoss market) from entry fill.
- Places orders LIVE when GUARD_ALLOW_LIVE=true.

ENV:
  REDIS_URL=redis://127.0.0.1:6379/0
  ORDER_EXEC_STREAM=orders:executed
  CONSUMER_GROUP=trading_guard
  CONSUMER_NAME=guard-1
  GUARD_ALLOW_LIVE=true
  TAKE_PROFIT_PCT=0.01
  STOP_LOSS_PCT=0.01
  BITVAVO_API_KEY=...
  BITVAVO_API_SECRET=...
  BITVAVO_OPERATOR_ID=1702
"""

import os, sys, json, time, logging, math
from decimal import Decimal, ROUND_DOWN
from redis import Redis
from redis.exceptions import ConnectionError, TimeoutError
from python_bitvavo_api.bitvavo import Bitvavo

LOG_LEVEL = os.getenv("LOG_LEVEL","INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("guard")

REDIS_URL         = os.getenv("REDIS_URL","redis://127.0.0.1:6379/0")
ORDER_EXEC_STREAM = os.getenv("ORDER_EXEC_STREAM","orders:executed")
CONSUMER_GROUP    = os.getenv("CONSUMER_GROUP","trading_guard")
CONSUMER_NAME     = os.getenv("CONSUMER_NAME","guard-1")
ALLOW_LIVE        = os.getenv("GUARD_ALLOW_LIVE","false").lower()=="true"

TP_PCT  = Decimal(os.getenv("TAKE_PROFIT_PCT","0.01"))
SL_PCT  = Decimal(os.getenv("STOP_LOSS_PCT","0.01"))

API_KEY    = os.getenv("BITVAVO_API_KEY","")
API_SECRET = os.getenv("BITVAVO_API_SECRET","")
OPID       = os.getenv("BITVAVO_OPERATOR_ID","")

# Conservative defaults if we can't fetch market metadata
DEFAULT_PRICE_DECIMALS = 5  # ICNT-EUR accepted 0.25859 -> 5 dp
DEFAULT_AMOUNT_DECIMALS = 8

def round_step(value: Decimal, decimals: int) -> str:
    q = Decimal(10) ** (-decimals)
    return str(value.quantize(q, rounding=ROUND_DOWN))

def get_client() -> Bitvavo | None:
    if not (API_KEY and API_SECRET):
        log.warning("BITVAVO_API_KEY/SECRET missing — will fail to place orders.")
        return None
    try:
        return Bitvavo({
            "APIKEY": API_KEY,
            "APISECRET": API_SECRET,
            "RESTURL": "https://api.bitvavo.com/v2"
        })
    except Exception as e:
        log.error("Failed to init Bitvavo client: %s", e)
        return None

def plan_orders(resp: dict) -> dict:
    """Build TP/SL plan from a Bitvavo fill response (market buy)."""
    fills = resp.get("fills") or []
    if not fills:
        return {"skip": True, "reason": "no-fills"}
    entry_price = Decimal(str(fills[0]["price"]))
    amount      = Decimal(str(fills[0]["amount"]))  # base amount filled

    tp_price = entry_price * (Decimal("1") + TP_PCT)
    sl_price = entry_price * (Decimal("1") - SL_PCT)

    # Round conservatively
    tp_price_s = round_step(tp_price, DEFAULT_PRICE_DECIMALS)
    sl_price_s = round_step(sl_price, DEFAULT_PRICE_DECIMALS)
    amt_s      = round_step(amount, DEFAULT_AMOUNT_DECIMALS)

    plan = {
      "market": resp["market"],
      "amount": amt_s,
      "entry_price": str(entry_price),
      "tp": {
        "market": resp["market"],
        "side": "sell",
        "orderType": "limit",
        "amount": amt_s,
        "price": tp_price_s,
        "timeInForce": "GTC",
        "postOnly": False,
      },
      "sl": {
        "market": resp["market"],
        "side": "sell",
        "orderType": "stopLoss",
        # Bitvavo requires BOTH amount and triggerAmount for stopLoss
        "amount": amt_s,
        "triggerType": "price",
        "triggerReference": "lastTrade",
        "triggerPrice": sl_price_s,
        "triggerAmount": amt_s,    # <-- critical fix
      },
    }
    if OPID:
        plan["tp"]["operatorId"] = int(OPID)
        plan["sl"]["operatorId"] = int(OPID)
    return plan

def place_orders(bv: Bitvavo, plan: dict) -> dict:
    if plan.get("skip"):
        return {"placed": False, "reason": plan.get("reason")}
    if not ALLOW_LIVE:
        return {"placed": False, "reason": "dry-guard", "plan": plan}

    tp_body = plan["tp"].copy()
    sl_body = plan["sl"].copy()
    market  = plan["market"]

    tpRes = slRes = None
    try:
        tpRes = bv.placeOrder(market, tp_body["side"], tp_body["orderType"], tp_body)
    except Exception as e:
        log.error("TP place exception: %s", e)
        return {"placed": False, "tpRes": None, "slRes": None, "plan": plan,
                "reason": f"tp-exception:{e}"}

    try:
        slRes = bv.placeOrder(market, sl_body["side"], sl_body["orderType"], sl_body)
    except Exception as e:
        log.error("SL place exception: %s", e)
        return {"placed": False, "tpRes": tpRes, "slRes": None, "plan": plan,
                "reason": f"sl-exception:{e}"}

    # If API returns an error object (dict with errorCode), mark as failed
    if isinstance(tpRes, dict) and tpRes.get("errorCode"):
        return {"placed": False, "tpRes": tpRes, "slRes": slRes, "plan": plan,
                "reason": f"tp-error:{tpRes.get('errorCode')}:{tpRes.get('error')}"}
    if isinstance(slRes, dict) and slRes.get("errorCode"):
        return {"placed": False, "tpRes": tpRes, "slRes": slRes, "plan": plan,
                "reason": f"sl-error:{slRes.get('errorCode')}:{slRes.get('error')}"}

    return {"placed": True, "tpRes": tpRes, "slRes": slRes, "plan": plan}

def read_stream_blocking(r: Redis, group: str, consumer: str):
    # Make sure group exists
    try:
        r.xgroup_create(name=ORDER_EXEC_STREAM, groupname=group, id="$", mkstream=True)
    except Exception:
        pass
    # Blocking read
    resp = r.xreadgroup(groupname=group, consumername=consumer,
                        streams={ORDER_EXEC_STREAM: ">"}, count=1, block=5000)
    if not resp:
        return None
    _, entries = resp[0]
    if not entries:
        return None
    return entries[0]  # (id, {fields})

def main():
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    bv = get_client()
    log.info("Guard startconfig | live=%s stream=%s group=%s consumer=%s",
             ALLOW_LIVE, ORDER_EXEC_STREAM, CONSUMER_GROUP, CONSUMER_NAME)

    while True:
        try:
            item = read_stream_blocking(r, CONSUMER_GROUP, CONSUMER_NAME)
            if not item:
                continue
            xid, fields = item
            try:
                resp = fields.get("response")
                if isinstance(resp, str):
                    resp = json.loads(resp)
                plan = plan_orders(resp)
                outcome = place_orders(bv, plan)
                log.info("GUARD PLAN id=%s plan=%s outcome=%s", xid,
                         json.dumps({k:v for k,v in plan.items() if k in ("market","amount","entry_price","tp","sl")}),
                         json.dumps(outcome))
            finally:
                # ack even if fail to avoid reprocessing loop
                r.xack(ORDER_EXEC_STREAM, CONSUMER_GROUP, xid)
        except (ConnectionError, TimeoutError):
            time.sleep(1.0)
        except KeyboardInterrupt:
            log.info("Guard netjes gestopt.")
            break
        except Exception as e:
            log.error("Onverwachte fout in main-loop: %s", e)
            time.sleep(1.0)

if __name__ == "__main__":
    main()
