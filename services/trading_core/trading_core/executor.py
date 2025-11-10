"""Core execution engine for the trading-core service."""
from __future__ import annotations

import datetime as dt
import os
import signal
import sys
import time
from typing import Any, Dict, Mapping, Optional, Tuple

import orjson as jsonf
from redis import Redis

from .decision import Decision, Intent

VERSION = "trading_core 2025-10-30 dyn-cap v2"


def _clean_env(value: Optional[str], default: Any) -> str:
    if value is None:
        return str(default)
    return str(value).split("#", 1)[0].strip()


def _env_float(name: str, default: float) -> float:
    return float(_clean_env(os.getenv(name), default))


def _env_int(name: str, default: int) -> int:
    return int(float(_clean_env(os.getenv(name), default)))


def _env_bool(name: str, default: bool) -> bool:
    value = _clean_env(os.getenv(name), str(default)).lower()
    return value in {"1", "true", "yes", "on"}


def build_default_conf() -> Dict[str, Any]:
    return {
        "REDIS_URL": _clean_env(os.getenv("REDIS_URL"), "redis://127.0.0.1:6379/0"),
        "DRY_RUN": _env_bool("DRY_RUN", True),
        "SIGNAL_STREAM": _clean_env(os.getenv("SIGNAL_STREAM"), "signals:baseline"),
        "CONSUMER_GROUP": _clean_env(os.getenv("CONSUMER_GROUP"), "trading_core"),
        "CONSUMER_NAME": _clean_env(os.getenv("CONSUMER_NAME"), "core"),
        "ORDER_OUTBOX_STREAM": _clean_env(os.getenv("ORDER_OUTBOX_STREAM"), "orders:shadow"),
        "EVENT_STREAM": _clean_env(os.getenv("EVENT_STREAM"), "trading:events"),
        "KILL_SWITCH_KEY": _clean_env(os.getenv("KILL_SWITCH_KEY"), "trading:kill"),
        "MAX_CONCURRENT_POS": _env_int("MAX_CONCURRENT_POS", 5),
        "MAX_GLOBAL_EXPOSURE_EUR": _env_float("MAX_GLOBAL_EXPOSURE_EUR", 0.0),
        "MAX_PER_ASSET_EUR": _env_float("MAX_PER_ASSET_EUR", 0.0),
        "PER_ASSET_FRAC": _env_float("PER_ASSET_FRAC", 0.0),
        "TP_SL_MODE": _clean_env(os.getenv("TP_SL_MODE"), "percent"),
        "TP_PCT": _env_float("TP_PCT", 2.0),
        "SL_PCT": _env_float("SL_PCT", 1.0),
        "TRAILING_PCT": _env_float("TRAILING_PCT", 0.0),
    }


KEY_EUR_AVAIL = "account:eur_available"
KEY_SLOT_BUDG = "account:slot_budget_eur"
KEY_EXPOSURE_H = "trading:exposure"
KEY_POSITIONS_H = "trading:positions"


class Executor:
    """Stateful trading-core executor that enforces the guard rails."""

    def __init__(self, redis: Redis | None = None, config: Mapping[str, Any] | None = None):
        self.conf: Dict[str, Any] = build_default_conf()
        if config:
            self.conf.update(dict(config))
        self.redis = redis or Redis.from_url(self.conf["REDIS_URL"], decode_responses=True)
        self._stop = False

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def now_iso() -> str:
        return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    def log_event(self, level: str, msg: str, where: str | None = None) -> None:
        payload = {"lvl": level, "msg": msg, "ts": self.now_iso()}
        if where:
            payload["where"] = where
        try:
            self.redis.xadd(self.conf["EVENT_STREAM"], payload)
        except Exception:
            pass

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self.redis.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception as exc:  # BUSYGROUP already exists
            if "BUSYGROUP" not in str(exc):
                raise

    def pos_count(self) -> int:
        try:
            return self.redis.hlen(KEY_POSITIONS_H)
        except Exception:
            return 0

    @staticmethod
    def sum_exposure(values: list[str]) -> float:
        total = 0.0
        for value in values:
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue
        return total

    def get_current_exposure(self) -> Tuple[float, Dict[str, float]]:
        try:
            mapping = self.redis.hgetall(KEY_EXPOSURE_H) or {}
        except Exception:
            mapping = {}
        per_asset: Dict[str, float] = {}
        for key, value in mapping.items():
            if key == "_global":
                continue
            try:
                per_asset[key] = float(value)
            except (TypeError, ValueError):
                continue
        cur_global = self.sum_exposure([f"{v}" for v in per_asset.values()])
        return cur_global, per_asset

    def eur_available(self) -> float:
        try:
            return float(self.redis.get(KEY_EUR_AVAIL) or 0.0)
        except Exception:
            return 0.0

    def slot_budget_eur(self) -> float:
        try:
            return float(self.redis.get(KEY_SLOT_BUDG) or 0.0)
        except Exception:
            return 0.0

    def compute_caps(self) -> Tuple[float, float]:
        cur_global, _ = self.get_current_exposure()
        eur_av = self.eur_available()

        if self.conf["MAX_GLOBAL_EXPOSURE_EUR"] > 0:
            gcap = float(self.conf["MAX_GLOBAL_EXPOSURE_EUR"])
        else:
            gcap = cur_global + eur_av

        if self.conf["MAX_PER_ASSET_EUR"] > 0:
            pacap = float(self.conf["MAX_PER_ASSET_EUR"])
        else:
            pacap = 0.0

        if self.conf["PER_ASSET_FRAC"] > 0:
            frac_cap = gcap * float(self.conf["PER_ASSET_FRAC"])
            pacap = frac_cap if pacap == 0.0 else min(pacap, frac_cap)

        slot_budget = self.slot_budget_eur()
        if slot_budget > 0:
            pacap = slot_budget if pacap == 0.0 else min(pacap, slot_budget)

        return gcap, pacap

    def blocked_by_guards(self, market: str, size_eur: float) -> Tuple[bool, str]:
        if (self.redis.get(self.conf["KILL_SWITCH_KEY"]) or "0") in {"1", "true", "on", "yes"}:
            return True, "kill_switch=ON"

        max_slots = int(self.conf["MAX_CONCURRENT_POS"])
        if max_slots > 0 and self.pos_count() >= max_slots:
            return True, f"slot_cap {self.pos_count()}>={max_slots}"

        cur_global, per_asset = self.get_current_exposure()
        cur_asset = float(per_asset.get(market, 0.0))
        gcap, pacap = self.compute_caps()

        if cur_global + size_eur > gcap + 1e-9:
            return True, f"global cap {cur_global + size_eur:.2f}>{gcap:.2f}"

        if pacap > 0 and (cur_asset + size_eur > pacap + 1e-9):
            return True, f"asset cap {cur_asset + size_eur:.2f}>{pacap:.2f}"

        eur_av = self.eur_available()
        if eur_av > 0 and size_eur > eur_av + 1e-9:
            return True, f"insufficient EUR_available {size_eur:.2f}>{eur_av:.2f}"

        return False, ""

    def write_order_outbox(self, intent: Intent) -> Mapping[str, Any]:
        order = {
            "ts": self.now_iso(),
            "version": VERSION,
            "dry_run": "true" if self.conf["DRY_RUN"] else "false",
            "action": "OPEN",
            "signal_id": intent.signal_id,
            "market": intent.market,
            "side": intent.side,
            "price": f"{intent.price:.8f}",
            "size_eur": f"{intent.size_eur:.2f}",
            "mode": self.conf["TP_SL_MODE"],
            "tp_pct": f"{self.conf['TP_PCT']:.4f}",
            "sl_pct": f"{self.conf['SL_PCT']:.4f}",
            "trail_pct": f"{self.conf['TRAILING_PCT']:.4f}",
        }
        payload = jsonf.dumps(order).decode("utf-8")
        self.redis.xadd(self.conf["ORDER_OUTBOX_STREAM"], {"data": payload})
        return order

    def bump_exposure(self, market: str, delta_eur: float) -> None:
        delta = float(f"{delta_eur:.8f}")
        self.redis.hincrbyfloat(KEY_EXPOSURE_H, market, delta)
        self.redis.hincrbyfloat(KEY_EXPOSURE_H, "_global", delta)
        self.redis.hincrbyfloat(KEY_POSITIONS_H, market, delta)

    # ---- signal processing ------------------------------------------
    def handle_signal(self, msg_id: str, fields: Mapping[str, Any]) -> Decision | None:
        intent = Intent.from_signal(msg_id, fields)
        if intent is None:
            self.log_event("WARN", "drop signal: invalid payload", "handle_signal")
            return None

        blocked, reason = self.blocked_by_guards(intent.market, intent.size_eur)
        if blocked:
            self.log_event("WARN", f"guard_block {intent.market} {reason}", "loop")
            return Decision(intent=intent, accepted=False, reason=reason)

        order = self.write_order_outbox(intent)
        self.bump_exposure(intent.market, intent.size_eur)
        self.log_event(
            "INFO",
            f"queued OPEN {intent.market} {intent.side} {intent.size_eur:.2f}€ @~{intent.price}",
            "loop",
        )
        return Decision(intent=intent, accepted=True, order=order)

    def consume_loop(self) -> None:
        stream = self.conf["SIGNAL_STREAM"]
        group = self.conf["CONSUMER_GROUP"]
        name = self.conf["CONSUMER_NAME"]

        self.ensure_group(stream, group)
        print(
            f"[core] start; {VERSION}; dry_run={self.conf['DRY_RUN']} "
            f"stream={stream} group={self.conf['CONSUMER_GROUP']}",
            file=sys.stderr,
            flush=True,
        )

        block_ms = 5000
        while not self._stop:
            try:
                resp = self.redis.xreadgroup(group, name, streams={stream: ">"}, count=50, block=block_ms)
                if not resp:
                    continue
                for _stream, entries in resp:
                    for msg_id, raw in entries:
                        try:
                            self.handle_signal(msg_id, raw)
                        except Exception as exc:
                            self.log_event("ERROR", f"{exc}", "loop")
                        finally:
                            try:
                                self.redis.xack(stream, group, msg_id)
                            except Exception:
                                pass
            except Exception as exc:
                self.log_event("ERROR", f"{exc}", "read_loop")
                time.sleep(1.0)

        print("[core] stopped", file=sys.stderr, flush=True)

    def _signal_stop(self, *_: Any) -> None:
        self._stop = True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._signal_stop)
        signal.signal(signal.SIGINT, self._signal_stop)
        self.consume_loop()


def main() -> None:  # pragma: no cover
    Executor().run()


__all__ = ["Executor", "Decision", "Intent", "main"]
