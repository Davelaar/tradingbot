"""Prometheus metrics exporter for the trader signal engine."""
from __future__ import annotations

import os
import random
import time

from prometheus_client import Counter, Gauge, start_http_server

SIGNALS_EMITTED = Counter(
    "ai_signals_total", "Aantal AI-signalen", ["type"]
)
SIGNAL_SCORE = Gauge(
    "ai_signal_score", "Gemiddelde score laatste minuut"
)


def observe_sample() -> None:
    SIGNALS_EMITTED.labels(random.choice(["buy", "sell", "hold"])).inc()
    SIGNAL_SCORE.set(random.uniform(0.0, 1.0))


def serve_forever() -> None:
    port = int(os.getenv("TRADER_SIGNAL_ENGINE_METRICS_PORT", "9103"))
    start_http_server(port)
    interval = float(os.getenv("TRADER_SIGNAL_ENGINE_METRICS_INTERVAL", "3"))
    while True:
        observe_sample()
        time.sleep(interval)


def main() -> None:  # pragma: no cover
    serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
