#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, time, logging, decimal
from typing import Any, Dict, List, Tuple
from redis import Redis
from redis.exceptions import ResponseError
from python_bitvavo_api.bitvavo import Bitvavo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
D = decimal.Decimal

# ---- Config via ENV ----
REDIS_URL    = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
ORDER_STREAM = os.getenv("ORDER_STREAM", "orders:live")
EXEC_STREAM  = os.getenv("EXEC_STREAM",  "orders:executed")
GROUP        = os.getenv("GROUP",        "trading_submitter")
CONSUMER     = os.getenv("CONSUMER",     "submitter-1")
DRY          = os.getenv("DRY", "0") in ("1","true","TRUE","yes","YES")

APIKEY    = os.getenv("BITVAVO_API_KEY", "")
APISECRET = os.getenv("BITVAVO_API_SECRET", "")
OPID      = os.getenv("BITVAVO_OPERATOR_ID", "")

def _is_errorish(obj: Any) -> bool:
    try:
        if isinstance(obj, dict):
            if "error" in obj or "errorCode" in obj:
                return True
            r = obj.get("response")
            if isinstance(r, dict) and ("error" in r or "errorCode" in r):
                return True
        return False
    except Exception:
        return False

def ensure_group(r: Redis) -> None:
    try:
        r.xgroup_create(name=ORDER_STREAM, groupname=GROUP, id="$", mkstream=True)
        logging.info("XGROUP CREATE %s %s", ORDER_STREAM, GROUP)
    except ResponseError as e:
        if "BUSYGROUP" in str(e) or "exists" in str(e):
            logging.info("XGROUP OK %s %s", ORDER_STREAM, GROUP)
        else:
            raise

def emit_executed(r: Redis, mid: str, status: str, obj: Any) -> None:
    try:
        r.xadd(
            EXEC_STREAM,
            {
                "status": status,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "mid": mid,
                "order": json.dumps(obj, separators=(",", ":"), default=str),
            },
        )
    except Exception as e:
        logging.error("emit_executed failed mid=%s err=%s", mid, e)

def parse_payload(raw_fields: Dict[str,str]) -> Dict[str, Any]:
    """Ondersteunt ofwel key 'data' met JSON, of direct losse velden."""
    if "data" in raw_fields:
        return json.loads(raw_fields["data"])
    d: Dict[str,Any] = {}
    for k,v in raw_fields.items():
        if k in ("market","side","orderType","mode","src","ts"):
            d[k] = v
        elif k in ("amount","price","size_eur","tp_pct","sl_pct","trail_pct"):
            d[k] = v
    return d

def build_request_body(p: Dict[str,Any]) -> Tuple[str,str,str,Dict[str,Any]]:
    """Maak Bitvavo request (market, side, orderType, body)."""
    market     = p.get("market")
    side       = p.get("side", "buy")
    order_type = p.get("orderType", "market")
    if not market or not side:
        raise ValueError("missing market/side in payload")

    body: Dict[str,Any] = {}
    # amount/price
    if "amount" in p and p["amount"] not in (None, "", "0", "0.0", "0.000000"):
        body["amount"] = str(p["amount"])
    if "price" in p and p["price"] not in (None, "", "0", "0.0", "0.000000"):
        body["price"] = None if p["price"] in (None, "null") else str(p["price"])

    # extra voor transparantie
    for k in ("mode","tp_pct","sl_pct","trail_pct","src","ts"):
        if k in p:
            body[k] = p[k]

    # operatorId toevoegen indien beschikbaar
    if OPID:
        body["operatorId"] = OPID

    return market, side, order_type, body

def main() -> None:
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    ensure_group(r)

    bv = None
    if not DRY:
        cfg = {
            "APIKEY": APIKEY,
            "APISECRET": APISECRET,
            "ACCESSWINDOW": 10000,
            "RECVWINDOW": 5000,
        }
        bv = Bitvavo(cfg)

    logging.info(
        "Submitter gestart | stream=%s group=%s consumer=%s live=%s",
        ORDER_STREAM, GROUP, CONSUMER, (not DRY),
    )

    while True:
        try:
            msgs: List[Tuple[str,List[Tuple[str,Dict[str,str]]]]] = r.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={ORDER_STREAM: '>'},
                count=20,
                block=5000
            )
        except ResponseError as e:
            if "NOGROUP" in str(e):
                ensure_group(r)
                continue
            logging.error("LOOP EXC %r", e)
            time.sleep(1)
            continue
        except Exception as e:
            logging.error("LOOP EXC %r", e)
            time.sleep(1)
            continue

        if not msgs:
            continue

        for _stream, entries in msgs:
            for mid, fields in entries:
                try:
                    payload = parse_payload(fields)
                except Exception as e:
                    logging.error("PARSE ERR id=%s fields=%r err=%r", mid, fields, e)
                    emit_executed(r, mid, "PARSE_ERR", {"error": str(e), "fields": fields})
                    r.xack(ORDER_STREAM, GROUP, mid)
                    continue

                if DRY:
                    fake = {
                        "market": payload.get("market","?"),
                        "side": payload.get("side","buy"),
                        "orderType": payload.get("orderType","market"),
                        "amount": str(payload.get("amount","0.000000")),
                        "price": payload.get("price", None),
                        "src": payload.get("src","submitter_dry"),
                    }
                    emit_executed(r, mid, "DRY_OK", fake)
                    r.xack(ORDER_STREAM, GROUP, mid)
                    continue

                # LIVE
                try:
                    mkt, side, ot, body = build_request_body(payload)
                    logging.info("OUT %s %s %s body=%s", mkt, side, ot, body)
                    # correcte Bitvavo signatuur:
                    result = bv.placeOrder(mkt, side, ot, body)
                    status = "LIVE_ERR" if _is_errorish(result) else "LIVE_OK"
                    emit_executed(
                        r, mid, status,
                        {
                            "request": {
                                "market": mkt, "side": side, "orderType": ot,
                                **{k: body[k] for k in ("amount","price","operatorId") if k in body}
                            },
                            "response": result
                        }
                    )
                except Exception as e:
                    logging.error("ORDER EXC id=%s payload=%r err=%r", mid, payload, e)
                    emit_executed(r, mid, "LIVE_ERR", {"request": payload, "exception": str(e)})
                finally:
                    try:
                        r.xack(ORDER_STREAM, GROUP, mid)
                    except Exception:
                        pass

if __name__ == "__main__":
    main()
