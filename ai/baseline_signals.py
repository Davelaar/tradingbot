"""Backwards compatible entrypoint for the trader signal engine."""
from services.trader_signal_engine.app.main import pump


if __name__ == "__main__":  # pragma: no cover
    pump()
