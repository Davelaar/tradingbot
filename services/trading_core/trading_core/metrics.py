"""Prometheus metrics exporters for the trading-core service."""
from __future__ import annotations

import os
import random
import time
from typing import Iterable, Sequence

from prometheus_client import Counter, Gauge, Histogram, start_http_server

ORDERS_TOTAL = Counter(
    "trading_orders_total", "Aantal geplaatste orders", ["pair", "side"]
)
FILLS_TOTAL = Counter(
    "trading_fills_total", "Aantal fills", ["pair"]
)
PNL_REALIZED = Gauge(
    "pnl_realized_eur_total", "Gerealiseerde PnL in euro"
)
ORDER_LATENCY = Histogram(
    "order_latency_seconds", "Order roundtrip latency (s)"
)

_DEF_PAIRS: tuple[str, ...] = ("BTC-EUR", "ETH-EUR", "GLMR-EUR")


class Metrics:
    """Runtime helper that keeps the exporter configuration bundled."""

    def __init__(self, pairs: Sequence[str] | None = None) -> None:
        self.pairs = tuple(pairs) if pairs is not None else _DEF_PAIRS

    def observe_sample(self) -> None:
        """Record a single simulated sample for the exposed metrics."""
        pair = random.choice(self.pairs)
        side = random.choice(["buy", "sell"])
        ORDERS_TOTAL.labels(pair, side).inc()
        FILLS_TOTAL.labels(pair).inc()
        PNL_REALIZED.set(random.uniform(-5, 15))
        ORDER_LATENCY.observe(random.uniform(0.1, 1.2))

    def serve_forever(self, port: int | None = None, interval: float | None = None) -> None:
        """Start the HTTP exporter and update metrics forever."""
        port = port or int(os.getenv("TRADING_CORE_METRICS_PORT", "9101"))
        interval = interval or float(os.getenv("TRADING_CORE_METRICS_INTERVAL", "5"))
        start_http_server(port)
        while True:
            self.observe_sample()
            time.sleep(interval)

    def run(self) -> None:
        """Convenience wrapper used by the CLI entrypoint."""
        self.serve_forever()


def observe_sample(pairs: Iterable[str] = _DEF_PAIRS) -> None:
    Metrics(tuple(pairs)).observe_sample()


def serve_forever() -> None:
    Metrics().serve_forever()


def main() -> None:  # pragma: no cover - thin CLI wrapper
    Metrics().run()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["Metrics", "observe_sample", "serve_forever", "main"]
