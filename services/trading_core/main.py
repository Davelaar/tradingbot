"""CLI entrypoint for the trading-core service."""
from __future__ import annotations

from .trading_core import Executor


def main() -> None:  # pragma: no cover
    Executor().run()


if __name__ == "__main__":  # pragma: no cover
    main()
