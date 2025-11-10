"""Trader universe selector service."""
from __future__ import annotations

import os
import time
from typing import Dict, List, Tuple

import orjson
from redis import Redis

CFG = {
    "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
    "SOURCE_STREAM": os.getenv("UNIVERSE_SOURCE", "bitvavo:ticker24h"),
    "TARGET_SET": os.getenv("UNIVERSE_SET", "trading:universe:active"),
    "EVENT_STREAM": os.getenv("UNIVERSE_STREAM", "trading:universe:events"),
    "MAX_MARKETS": int(os.getenv("UNIVERSE_MAX", "12")),
    "MIN_VOLUME": float(os.getenv("UNIVERSE_MIN_VOL", "1000")),
    "REFRESH_SEC": float(os.getenv("UNIVERSE_REFRESH_SEC", "60")),
}

redis_client = Redis.from_url(CFG["REDIS_URL"], decode_responses=True)


def _parse(fields: Dict[str, str]) -> Tuple[str, float]:
    raw = fields.get("data")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    market = ""
    volume = 0.0
    if isinstance(raw, str):
        try:
            payload = orjson.loads(raw)
            market = payload.get("market") or payload.get("marketId") or payload.get("pair") or ""
            volume = float(
                payload.get("volume")
                or payload.get("baseVolume")
                or payload.get("volume24h")
                or 0.0
            )
        except orjson.JSONDecodeError:
            pass
    return market, volume


def refresh_universe() -> List[str]:
    entries = redis_client.xrevrange(CFG["SOURCE_STREAM"], count=500)
    scores: Dict[str, float] = {}
    for _, fields in entries:
        market, volume = _parse(fields)
        if not market:
            continue
        scores[market] = max(volume, scores.get(market, 0.0))
    selected = [m for m, v in sorted(scores.items(), key=lambda item: item[1], reverse=True) if v >= CFG["MIN_VOLUME"]]
    selected = selected[: CFG["MAX_MARKETS"]]
    if selected:
        pipe = redis_client.pipeline()
        pipe.delete(CFG["TARGET_SET"])
        if selected:
            pipe.sadd(CFG["TARGET_SET"], *selected)
        pipe.execute()
        redis_client.xadd(
            CFG["EVENT_STREAM"],
            {"data": orjson.dumps({"markets": selected, "ts": time.time()}).decode()},
            maxlen=1000,
            approximate=True,
        )
    return selected


def main() -> None:  # pragma: no cover
    while True:
        refresh_universe()
        time.sleep(CFG["REFRESH_SEC"])


if __name__ == "__main__":  # pragma: no cover
    main()
