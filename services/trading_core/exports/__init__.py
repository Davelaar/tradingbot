"""Descriptions of the data exports produced by the trading-core service."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportDescriptor:
    name: str
    channel: str
    description: str


EXPORTS: tuple[ExportDescriptor, ...] = (
    ExportDescriptor(
        name="orders_shadow",
        channel="orders:shadow",
        description=(
            "Outbox stream met orders in dry-run/shadow-modus; dit is het primaire "
            "resultaat van de trading-core guards."
        ),
    ),
    ExportDescriptor(
        name="orders_signals",
        channel="orders:signals",
        description="Historische auditstream met signalen en guard-uitkomsten.",
    ),
    ExportDescriptor(
        name="trading_events",
        channel="trading:events",
        description="Event-log met guardmeldingen, errors en state-wijzigingen.",
    ),
)


__all__ = ["ExportDescriptor", "EXPORTS"]
