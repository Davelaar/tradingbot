"""Decision and intent dataclasses for the trading-core service."""
from __future__ import annotations

from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import orjson


@dataclass(frozen=True, slots=True)
class Intent:
    """Baseline intent payload parsed from the signal stream."""

    signal_id: str
    market: str
    side: str
    price: float
    size_eur: float
    score: float | None = None
    reasons: tuple[str, ...] = tuple()
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_signal(cls, signal_id: str, fields: Mapping[str, Any]) -> "Intent | None":
        """Build an :class:`Intent` from a Redis stream payload."""
        market = (fields.get("market") or "").strip()
        side = (fields.get("side") or "").strip().lower()
        if not market or side not in {"buy", "sell"}:
            return None

        def _flt(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        price = _flt(fields.get("price")) or 0.0
        size_eur = _flt(fields.get("size_eur")) or 0.0
        score = _flt(fields.get("score"))

        def _json(field: str) -> Mapping[str, Any] | Sequence[str]:
            raw = fields.get(field)
            if not raw:
                return {}
            try:
                value = orjson.loads(raw)
                if isinstance(value, dict):
                    return value
                if isinstance(value, (list, tuple)):
                    return value
            except orjson.JSONDecodeError:
                pass
            return {}

        raw_reasons = _json("reasons")
        if isinstance(raw_reasons, SequenceABC) and not isinstance(raw_reasons, (str, bytes)):
            reasons: tuple[str, ...] = tuple(str(r) for r in raw_reasons)
        else:
            reasons = tuple()

        raw_details = _json("details")
        details = raw_details if isinstance(raw_details, Mapping) else {}

        intent_cls = classify_intent(details)
        return intent_cls(
            signal_id=signal_id,
            market=market,
            side=side,
            price=price,
            size_eur=size_eur,
            score=score,
            reasons=reasons,
            details=details,
        )


@dataclass(frozen=True, slots=True)
class MomentumIntent(Intent):
    """Momentum focused intent parsed from volatility-based signals."""

    momentum_score: float | None = None


@dataclass(frozen=True, slots=True)
class MeanReversionIntent(Intent):
    """Mean-reversion intent derived from wick-based signals."""

    deviation_score: float | None = None


IntentType = type[Intent]


def classify_intent(details: Mapping[str, Any]) -> IntentType:
    """Return the specialised intent class derived from signal details."""
    if not details:
        return Intent

    wick_ratio = details.get("wick_ratio")
    if isinstance(wick_ratio, (int, float)):
        return MeanReversionIntent

    vol_std = details.get("vol_std")
    if isinstance(vol_std, (int, float)):
        return MomentumIntent

    return Intent


@dataclass(slots=True)
class Decision:
    """Outcome of evaluating an intent against the guard rails."""

    intent: Intent
    accepted: bool
    reason: str | None = None
    order: Mapping[str, Any] | None = None

    def to_order(self) -> Mapping[str, Any] | None:
        """Return the outbound order payload if the decision passed."""
        return self.order


__all__ = [
    "Intent",
    "MomentumIntent",
    "MeanReversionIntent",
    "Decision",
]
