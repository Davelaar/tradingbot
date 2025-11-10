"""Utilities to persist Bitvavo websocket batches to Parquet datasets.

The blueprint prescribes Redis Streams as the realtime transport and
Parquet as the durable landing zone.  The sink keeps the interface tiny so
that ingest scripts can drop batches without having to know about the
filesystem layout or pyarrow internals.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import threading
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Mapping

import orjson as jsonf
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class ParquetConfig:
    base_dir: pathlib.Path

    @classmethod
    def from_env(cls, env_var: str = "PARQUET_DIR", default: str = "/srv/trading/storage/parquet") -> "ParquetConfig":
        base = pathlib.Path(os.getenv(env_var, default)).expanduser()
        return cls(base)


class ParquetSink:
    """Append-only Parquet writer for websocket event batches."""

    _SCHEMA = pa.schema(
        [
            ("ingested_at", pa.timestamp("us")),
            ("event", pa.string()),
            ("market", pa.string()),
            ("payload", pa.string()),
        ]
    )

    def __init__(self, config: ParquetConfig):
        self._config = config
        self._lock = threading.Lock()

    def _daily_dir(self, event: str) -> pathlib.Path:
        day = dt.datetime.utcnow().strftime("%Y-%m-%d")
        target = self._config.base_dir / day / event
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _filename(self, event: str, market: str) -> str:
        safe_market = market.replace("/", "-") or "unknown"
        ts = dt.datetime.utcnow().strftime("%H%M%S")
        token = uuid.uuid4().hex[:10]
        return f"{safe_market}-{ts}-{token}.parquet"

    def write(self, event: str, market: str, rows: Iterable[Mapping[str, object]]) -> None:
        batch: List[Mapping[str, object]] = list(rows)
        if not batch:
            return

        payload_rows = []
        now = dt.datetime.utcnow()
        for row in batch:
            payload_rows.append(
                {
                    "ingested_at": now,
                    "event": event,
                    "market": market,
                    "payload": jsonf.dumps(row).decode("utf-8"),
                }
            )

        table = pa.Table.from_pylist(payload_rows, schema=self._SCHEMA)
        directory = self._daily_dir(event)
        file_path = directory / self._filename(event, market)

        with self._lock:
            pq.write_table(table, file_path)


__all__ = ["ParquetConfig", "ParquetSink"]
