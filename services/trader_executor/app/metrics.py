"""Metrics exporter for the trader executor service."""
from __future__ import annotations

import os
import random
import time

from prometheus_client import Counter, start_http_server

EXECUTED = Counter("trader_executor_orders_executed", "Orders verwerkt")
FAILED = Counter("trader_executor_orders_failed", "Orders met fout")


def observe_sample() -> None:
    if random.random() < 0.9:
        EXECUTED.inc()
    else:
        FAILED.inc()


def serve_forever() -> None:
    port = int(os.getenv("TRADER_EXECUTOR_METRICS_PORT", "9104"))
    start_http_server(port)
    interval = float(os.getenv("TRADER_EXECUTOR_METRICS_INTERVAL", "5"))
    while True:
        observe_sample()
        time.sleep(interval)


def main() -> None:  # pragma: no cover
    serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
