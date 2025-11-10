"""Exports produced by the trader signal engine service."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportDescriptor:
    name: str
    channel: str
    description: str


EXPORTS: tuple[ExportDescriptor, ...] = (
    ExportDescriptor(
        name="baseline_signals",
        channel="signals:baseline",
        description="Redis stream met gefilterde long/short signalen per markt.",
    ),
)


__all__ = ["ExportDescriptor", "EXPORTS"]
