"""Exports produced by the trader PnL orchestrator."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportDescriptor:
    name: str
    channel: str
    description: str


EXPORTS: tuple[ExportDescriptor, ...] = (
    ExportDescriptor(
        name="pnl_daily",
        channel="trading:pnl:daily",
        description="Redis hash met cumulatieve PnL per markt per dag.",
    ),
    ExportDescriptor(
        name="pnl_events",
        channel="trading:pnl:events",
        description="Stream met individuele PnL-delta's voor auditing.",
    ),
)


__all__ = ["ExportDescriptor", "EXPORTS"]
