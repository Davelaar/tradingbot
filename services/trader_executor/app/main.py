"""Trader executor service.

The blueprint splits responsibilities across dedicated services.
This executor consumes order intents from the trading-core outbox and
publishes the simulated execution result to Redis streams.  The actual
Bitvavo REST integration can be plugged in later; voor nu behouden we
het dry-run pad zodat bestaande workflows blijven werken.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable

import orjson
from redis import Redis

CFG = {
    "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
    "ORDER_INBOX": os.getenv("ORDER_INBOX", "orders:shadow"),
    "EXEC_STREAM": os.getenv("EXEC_STREAM", "orders:executed"),
    "ERROR_STREAM": os.getenv("ERROR_STREAM", "orders:errors"),
    "CONSUMER_GROUP": os.getenv("EXECUTOR_GROUP", "trader_executor"),
    "CONSUMER_NAME": os.getenv("EXECUTOR_NAME", "executor"),
    "POLL_MS": int(float(os.getenv("EXECUTOR_POLL_MS", "1000"))),
}

redis_client = Redis.from_url(CFG["REDIS_URL"], decode_responses=True)


def _ensure_group(stream: str, group: str) -> None:
    try:
        redis_client.xgroup_create(stream, group, id="0-0", mkstream=True)
    except Exception as exc:  # pragma: no cover - BUSYGROUP is expected
        if "BUSYGROUP" not in str(exc):
            raise


def _parse_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = raw.get("data")
    if isinstance(data, (bytes, bytearray)):
        data = data.decode()
    if isinstance(data, str):
        try:
            return orjson.loads(data)
        except orjson.JSONDecodeError:
            return {"raw": data}
    return raw


def _emit(stream: str, payload: Dict[str, Any]) -> None:
    redis_client.xadd(stream, {"data": orjson.dumps(payload).decode()})


def _handle_order(msg_id: str, payload: Dict[str, Any]) -> None:
    envelope = _parse_payload(payload)
    response = {
        "id": envelope.get("id", msg_id),
        "market": envelope.get("market"),
        "side": envelope.get("side"),
        "size": envelope.get("size"),
        "price": envelope.get("price"),
        "dry_run": True,
        "status": "accepted",
        "ts": time.time(),
    }
    _emit(CFG["EXEC_STREAM"], response)


def _handle_messages(messages: Iterable[tuple[str, Dict[str, Any]]]) -> None:
    for msg_id, payload in messages:
        try:
            _handle_order(msg_id, payload)
        except Exception as exc:  # pragma: no cover - defensive
            _emit(CFG["ERROR_STREAM"], {"id": msg_id, "error": str(exc)})
        finally:
            redis_client.xack(CFG["ORDER_INBOX"], CFG["CONSUMER_GROUP"], msg_id)


def run_once() -> None:
    resp = redis_client.xreadgroup(
        CFG["CONSUMER_GROUP"],
        CFG["CONSUMER_NAME"],
        {CFG["ORDER_INBOX"]: ">"},
        count=100,
        block=CFG["POLL_MS"],
    )
    for _, messages in resp or []:
        _handle_messages(messages)


def main() -> None:  # pragma: no cover
    _ensure_group(CFG["ORDER_INBOX"], CFG["CONSUMER_GROUP"])
    while True:
        run_once()


if __name__ == "__main__":  # pragma: no cover
    main()
