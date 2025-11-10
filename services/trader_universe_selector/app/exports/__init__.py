"""Exports produced by the trader universe selector."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportDescriptor:
    name: str
    channel: str
    description: str


EXPORTS: tuple[ExportDescriptor, ...] = (
    ExportDescriptor(
        name="universe_set",
        channel="trading:universe:active",
        description="Redis set met markten die in de volgende sessie actief mogen traden.",
    ),
    ExportDescriptor(
        name="universe_events",
        channel="trading:universe:events",
        description="Stream met universe-wijzigingen voor downstream consumers.",
    ),
)


__all__ = ["ExportDescriptor", "EXPORTS"]
