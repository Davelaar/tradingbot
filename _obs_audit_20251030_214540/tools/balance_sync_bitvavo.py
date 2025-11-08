#!/usr/bin/env python3
import os, time, sys
from redis import Redis
from python_bitvavo_api.bitvavo import Bitvavo

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
SLOTS     = int(float(os.getenv("SLOTS", "5")))   # hoeveel slots wil je verdelen
POLL_SEC  = int(float(os.getenv("BALANCE_SYNC_INTERVAL", "5")))

APIKEY    = os.getenv("BITVAVO_API_KEY")
APISECRET = os.getenv("BITVAVO_API_SECRET")
RESTURL   = os.getenv("BITVAVO_REST_URL", "https://api.bitvavo.com")

def log(msg): print(f"[balance-sync] {msg}", flush=True)

if not APIKEY or not APISECRET:
    log("BITVAVO_API_KEY/SECRET ontbreekt; stop.")
    sys.exit(0)

r = Redis.from_url(REDIS_URL, decode_responses=True)
bv = Bitvavo({"APIKEY": APIKEY, "APISECRET": APISECRET, "RESTURL": RESTURL})

def run_once():
    # 1) haal alle balances op
    bals = bv.balance({})
    avail = {}
    inorder = {}
    for b in bals:
        sym = b.get("symbol")
        if not sym: continue
        try:
            avail[sym] = float(b.get("available", 0) or 0)
            inorder[sym] = float(b.get("inOrder", 0) or 0)
        except Exception:
            continue

    # 2) bewaar ruwe per-asset balans
    if avail:
        r.hset("account:available", mapping={k: f"{v:.8f}" for k,v in avail.items()})
    if inorder:
        r.hset("account:inorder",   mapping={k: f"{v:.8f}" for k,v in inorder.items()})

    # 3) EUR beschikbaar = geld voor nieuwe entries (excl. open orders)
    eur_avail = float(avail.get("EUR", 0.0))
    r.set("account:eur_available", f"{eur_avail:.2f}")

    # 4) slot budget = EUR_available / SLOTS
    slot_budget = eur_avail / max(SLOTS, 1)
    r.set("account:slot_budget_eur", f"{slot_budget:.2f}")

    log(f"EUR_available={eur_avail:.2f}; slots={SLOTS}; slot_budget={slot_budget:.2f}")

def main():
    while True:
        try:
            run_once()
        except Exception as e:
            log(f"error: {e}")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
