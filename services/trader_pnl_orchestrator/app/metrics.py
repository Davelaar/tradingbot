"""Metrics exporter for the trader PnL orchestrator."""
from __future__ import annotations

import os
import random
import time

from prometheus_client import Gauge, start_http_server

PNL_DAILY = Gauge("trader_pnl_daily_eur", "Dagelijkse PnL in euro")


def observe_sample() -> None:
    PNL_DAILY.set(random.uniform(-50, 150))


def serve_forever() -> None:
    port = int(os.getenv("TRADER_PNL_METRICS_PORT", "9105"))
    start_http_server(port)
    interval = float(os.getenv("TRADER_PNL_METRICS_INTERVAL", "10"))
    while True:
        observe_sample()
        time.sleep(interval)


def main() -> None:  # pragma: no cover
    serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
