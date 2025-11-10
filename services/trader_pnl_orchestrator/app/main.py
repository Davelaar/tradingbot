"""Trader PnL orchestrator service."""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict, Iterable

import orjson
from redis import Redis

CFG = {
    "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
    "EXEC_STREAM": os.getenv("EXEC_STREAM", "orders:executed"),
    "PNL_HASH": os.getenv("PNL_HASH", "trading:pnl:daily"),
    "PNL_STREAM": os.getenv("PNL_STREAM", "trading:pnl:events"),
    "CONSUMER_GROUP": os.getenv("PNL_GROUP", "trader_pnl"),
    "CONSUMER_NAME": os.getenv("PNL_NAME", "orchestrator"),
    "POLL_MS": int(float(os.getenv("PNL_POLL_MS", "1000"))),
}

redis_client = Redis.from_url(CFG["REDIS_URL"], decode_responses=True)


def _ensure_group() -> None:
    try:
        redis_client.xgroup_create(CFG["EXEC_STREAM"], CFG["CONSUMER_GROUP"], id="0-0", mkstream=True)
    except Exception as exc:  # pragma: no cover
        if "BUSYGROUP" not in str(exc):
            raise


def _parse(entry: Dict[str, Any]) -> Dict[str, Any]:
    data = entry.get("data")
    if isinstance(data, (bytes, bytearray)):
        data = data.decode()
    if isinstance(data, str):
        try:
            return orjson.loads(data)
        except orjson.JSONDecodeError:
            return {"raw": data}
    return entry


def _pnl_delta(order: Dict[str, Any]) -> float:
    try:
        price = float(order.get("fill_price") or order.get("price") or 0.0)
        size = float(order.get("fill_size") or order.get("size") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    side = (order.get("side") or "buy").lower()
    notional = price * size
    return notional if side == "sell" else -notional


def _emit(event: Dict[str, Any]) -> None:
    redis_client.xadd(CFG["PNL_STREAM"], {"data": orjson.dumps(event).decode()})


def _handle(messages: Iterable[tuple[str, Dict[str, Any]]]) -> None:
    for msg_id, raw in messages:
        try:
            order = _parse(raw)
            market = order.get("market") or "UNKNOWN"
            delta = _pnl_delta(order)
            day = dt.datetime.utcnow().strftime("%Y-%m-%d")
            key = f"{day}:{market}"
            if delta:
                redis_client.hincrbyfloat(CFG["PNL_HASH"], key, delta)
            _emit({"id": order.get("id", msg_id), "market": market, "delta": delta, "ts": dt.datetime.utcnow().isoformat() + "Z"})
        finally:
            redis_client.xack(CFG["EXEC_STREAM"], CFG["CONSUMER_GROUP"], msg_id)


def run_once() -> None:
    resp = redis_client.xreadgroup(
        CFG["CONSUMER_GROUP"],
        CFG["CONSUMER_NAME"],
        {CFG["EXEC_STREAM"]: ">"},
        count=100,
        block=CFG["POLL_MS"],
    )
    for _, messages in resp or []:
        _handle(messages)


def main() -> None:  # pragma: no cover
    _ensure_group()
    while True:
        run_once()


if __name__ == "__main__":  # pragma: no cover
    main()
