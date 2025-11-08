#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fees_sync_bitvavo.py — haalt account fees (maker/taker) op via REST en zet ze in Redis.
- Leest env: BITVAVO_API_KEY / BITVAVO_API_SECRET (fallback: APIKEY / APISECRET)
- REST only (geen WS-auth nodig)
- Schrijft: fees:account:maker_bps, fees:account:taker_bps
- Optioneel: respecteert REDIS_URL, BITVAVO_REST_URL, INTERVAL_SEC
"""

import os, time, datetime as dt
from typing import Optional, Dict, Any
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

VERSION = "fees_sync_bitvavo 2025-10-30 v2"

def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

def _clean(v: Optional[str], default: str = "") -> str:
    if v is None: return default
    return v.split("#", 1)[0].strip()

def env_f(name: str, default: float) -> float:
    v = _clean(os.getenv(name), str(default))
    try: return float(v)
    except: return default

def log(lvl: str, msg: str, where: str = "fees_sync"):
    print(f"{now_iso()} [{lvl}] {where}: {msg}", flush=True)

def get_keys() -> Dict[str, str]:
    # primair BITVAVO_*; fallback legacy APIKEY/APISECRET
    ak = _clean(os.getenv("BITVAVO_API_KEY"), _clean(os.getenv("APIKEY"), ""))
    sk = _clean(os.getenv("BITVAVO_API_SECRET"), _clean(os.getenv("APISECRET"), ""))
    return {"key": ak, "secret": sk}

def fetch_fees(bv: Bitvavo) -> Dict[str, int]:
    """
    Haal account() op; normaliseer maker/taker naar basispunten (bps).
    Bitvavo geeft doorgaans decimalen (0.0025 = 0.25%).
    """
    acct: Dict[str, Any] = bv.account()
    fees = acct.get("fees") or {}
    maker = fees.get("maker")
    taker = fees.get("taker")
    if maker is None or taker is None:
        raise RuntimeError(f"account() bevat geen fees: {acct}")

    maker_bps = int(round(float(maker) * 10000))
    taker_bps = int(round(float(taker) * 10000))
    return {"maker_bps": maker_bps, "taker_bps": taker_bps}

def one_sync():
    keys = get_keys()
    if len(keys["key"]) != 64:
        raise RuntimeError("BITVAVO_API_KEY (of fallback APIKEY) lijkt onjuist: verwacht lengte 64.")

    rest = _clean(os.getenv("BITVAVO_REST_URL"), "https://api.bitvavo.com/v2")
    rurl = _clean(os.getenv("REDIS_URL"), "redis://127.0.0.1:6379/0")
    r = Redis.from_url(rurl, decode_responses=True)

    bv = Bitvavo({"APIKEY": keys["key"], "APISECRET": keys["secret"], "RESTURL": rest})
    fees = fetch_fees(bv)

    r.mset({
        "fees:account:maker_bps": fees["maker_bps"],
        "fees:account:taker_bps": fees["taker_bps"],
    })
    log("INFO", f"updated fees account maker={fees['maker_bps']}bps taker={fees['taker_bps']}bps")

def main():
    interval = int(env_f("INTERVAL_SEC", 300))  # default elke 5 min
    log("INFO", f"start {VERSION}; interval={interval}s; REST only")
    while True:
        try:
            one_sync()
        except Exception as e:
            log("ERROR", f"{e!r}")
        time.sleep(interval)

if __name__ == "__main__":
    main()