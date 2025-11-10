"""Exports produced by the trader executor."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportDescriptor:
    name: str
    channel: str
    description: str


EXPORTS: tuple[ExportDescriptor, ...] = (
    ExportDescriptor(
        name="orders_executed",
        channel="orders:executed",
        description="Stream met uitgevoerde orders (dry-run of live).",
    ),
    ExportDescriptor(
        name="orders_errors",
        channel="orders:errors",
        description="Stream met execution errors en retriable failures.",
    ),
)


__all__ = ["ExportDescriptor", "EXPORTS"]
