#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
order_submit_bitvavo.py — Production-grade Bitvavo order submitter

✅ Functie
- Leest live orders uit Redis stream `orders:live`
- Plaatst orders via Bitvavo REST API (market of limit)
- Schrijft resultaten terug naar Redis:
    - Succes: stream `orders:executed`
    - Fout:   stream `trading:events` met lvl=error
- Idempotent: herstart veilig; gebruikt XREADGROUP
"""

import os
import sys
import time
import signal
import orjson as json
from typing import Dict, Any, Optional
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

VERSION = "submitter v1.0.0 (2025-10-30)"
RUNNING = True


# -----------------------------------------------------
# Helpers
# -----------------------------------------------------
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_env(path: str) -> Dict[str, str]:
    """Eenvoudige dotenv-loader"""
    env: Dict[str, str] = {}
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def ev(r: Redis, lvl: str, tag: str, msg: Any):
    """Schrijf naar trading:events"""
    payload = {
        "lvl": lvl,
        "tag": tag,
        "msg": msg if isinstance(msg, str) else json.dumps(msg),
        "ts": now_iso(),
    }
    try:
        r.xadd("trading:events", payload)
    except Exception:
        pass


def place_order(bv: Bitvavo, market: str, side: str, type_: str,
                amount: float, price: Optional[float] = None) -> Dict[str, Any]:
    """Stuurt order naar Bitvavo"""
    data = {"market": market, "side": side, "type": type_, "amount": str(amount)}
    if type_ == "limit" and price is not None:
        data["price"] = str(price)
    return bv.placeOrder(data)


# -----------------------------------------------------
# Init
# -----------------------------------------------------
def init_bitvavo() -> Bitvavo:
    env = {}
    env.update(load_env("/srv/trading/.env.trading"))
    env.update(load_env("/srv/trading/secrets/bitvavo.env"))
    key = env.get("BITVAVO_API_KEY", "")
    sec = env.get("BITVAVO_API_SECRET", "")
    if not key or not sec or "__VUL_HIER_IN__" in key:
        print("❌ Missing Bitvavo API credentials", file=sys.stderr)
        sys.exit(1)
    bv = Bitvavo({
        "APIKEY": key,
        "APISECRET": sec,
        "RESTURL": env.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2"),
        "WSURL": env.get("BITVAVO_WS_URL", "wss://ws.bitvavo.com/v2/")
    })
    return bv


def init_redis() -> Redis:
    env = load_env("/srv/trading/.env.trading")
    return Redis.from_url(env.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
                          decode_responses=True)


# -----------------------------------------------------
# Main Loop
# -----------------------------------------------------
def main():
    print(f"[submitter] start; {VERSION}", flush=True)
    bv = init_bitvavo()
    r = init_redis()
    ORDER_STREAM = os.getenv("ORDER_OUTBOX_STREAM", "orders:live")
    EXEC_STREAM = "orders:executed"
    GROUP = "submitters"
    CONSUMER = f"submitter-{os.getpid()}"

    # Zorg voor consumer group
    try:
        r.xgroup_create(ORDER_STREAM, GROUP, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    # Graceful stop
    def stop(sig, frame):
        global RUNNING
        RUNNING = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # Loop
    while RUNNING:
        try:
            resp = r.xreadgroup(GROUP, CONSUMER, {ORDER_STREAM: ">"}, count=10, block=2000)
            if not resp:
                continue
            for stream, messages in resp:
                for msg_id, kv in messages:
                    market = kv.get("market")
                    side = kv.get("side")
                    type_ = kv.get("type", "market")
                    amount = float(kv.get("amount", "0") or "0")
                    price = float(kv["price"]) if "price" in kv else None

                    try:
                        res = place_order(bv, market, side, type_, amount, price)
                        r.xadd(EXEC_STREAM, {
                            "id": msg_id,
                            "market": market,
                            "side": side,
                            "type": type_,
                            "amount": amount,
                            "price": price or "",
                            "ts": now_iso(),
                            "resp": json.dumps(res),
                        })
                        ev(r, "info", "submit-ok", {"market": market, "side": side})
                    except Exception as e:
                        ev(r, "error", "order-failed", {
                            "market": market,
                            "side": side,
                            "err": f"{type(e).__name__}: {e}"
                        })
                    finally:
                        r.xack(ORDER_STREAM, GROUP, msg_id)

        except Exception as e:
            ev(r, "error", "submitter-loop", {"err": f"{type(e).__name__}: {e}"})
            time.sleep(2)

    print("[submitter] stopped", flush=True)


# -----------------------------------------------------
# Entrypoint
# -----------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[submitter] stopped (keyboard)", flush=True)