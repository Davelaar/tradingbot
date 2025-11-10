"""Metrics exporter for the trader universe selector."""
from __future__ import annotations

import os
import random
import time

from prometheus_client import Gauge, start_http_server

UNIVERSE_SIZE = Gauge("trader_universe_size", "Aantal markten in de actieve universe")


def observe_sample() -> None:
    UNIVERSE_SIZE.set(random.randint(3, 20))


def serve_forever() -> None:
    port = int(os.getenv("TRADER_UNIVERSE_METRICS_PORT", "9106"))
    start_http_server(port)
    interval = float(os.getenv("TRADER_UNIVERSE_METRICS_INTERVAL", "15"))
    while True:
        observe_sample()
        time.sleep(interval)


def main() -> None:  # pragma: no cover
    serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
