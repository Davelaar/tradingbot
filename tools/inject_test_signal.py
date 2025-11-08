#!/usr/bin/env python3
# Minimal Redis XADD injector zonder redis-cli.
# Leest env vars voor flexibiliteit, heeft veilige defaults.
import os, sys, json, time
from redis import Redis
from redis.exceptions import ResponseError

STREAM = os.environ.get("STREAM", "signals:baseline")
GROUP  = os.environ.get("GROUP",  "trading_submitter")

MARKET = os.environ.get("MARKET", "HONEY-EUR")
SIDE   = os.environ.get("SIDE",   "buy")
PRICE  = float(os.environ.get("PRICE", "1.23"))
SIZE   = float(os.environ.get("SIZE",  "5.0"))
TP_PCT = float(os.environ.get("TP_PCT", "0.008"))
SL_PCT = float(os.environ.get("SL_PCT", "0.006"))
REASON = os.environ.get("REASON", "dry_probe")

r = Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)

# Zorg dat stream + consumer group bestaan (zoals core verwacht)
try:
    r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
except ResponseError as e:
    if "BUSYGROUP" not in str(e):
        raise

payload = {
    "market": MARKET,
    "side": SIDE,
    "price": str(PRICE),
    "size": str(SIZE),
    "tp_pct": str(TP_PCT),
    "sl_pct": str(SL_PCT),
    "reason": REASON,
    "ts": str(int(time.time()*1000)),
}
msg_id = r.xadd(STREAM, payload, id="*", mkstream=True)
length = r.xlen(STREAM)
print(f"XADD -> {msg_id} (stream len={length})")
