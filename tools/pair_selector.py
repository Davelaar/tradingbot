#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pair_selector.py — hardened, no-fake, no-assumptions
- Leest uitsluitend ECHTE data uit Redis stream: signals:baseline
- Filtert assets met deny-lijst (ENV PAIRSEL_DENY_BASES, CSV van BASE-symbols)
- Schrijft alleen non-empty selectie: ai:active_markets (SET) + ai:active_markets:list (LIST)
- Hysterese: behoud eerdere keuzes waar mogelijk (stabieler)
- Prometheus metrics op :PAIRSEL_PROM_PORT (disablet zichzelf als de poort bezet is)
"""

from __future__ import annotations
import os, sys, time, traceback
from datetime import datetime, timezone
from collections import Counter
from typing import List

# ---------- Config via ENV ----------
REDIS_URL             = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_CONCURRENCY       = int(os.getenv("PAIRSEL_MAX_CONCURRENCY", "5"))
WINDOW_SEC            = int(os.getenv("PAIRSEL_WINDOW_SEC", "900"))     # 15 min
MIN_COUNT             = int(os.getenv("PAIRSEL_MIN_COUNT", "3"))
PROM_PORT             = int(os.getenv("PAIRSEL_PROM_PORT", "9110"))
SLEEP_SEC             = float(os.getenv("PAIRSEL_SLEEP_SEC", "5.0"))
DENY_BASES_CSV        = os.getenv("PAIRSEL_DENY_BASES", "")  # bv: "BTC,ETH,BNB,ADA,SOL,XRP,USDT,USDC,EUR,USD,DAI"

# ---------- Dependencies ----------
try:
    from redis import Redis
except Exception as e:
    print(f"[pairsel] FATAL: python-redis ontbreekt: {e}", file=sys.stderr)
    sys.exit(1)

# Metrics optioneel: nooit crashen als module/poort niet kan
_metrics_enabled = True
try:
    from prometheus_client import start_http_server, Gauge, Counter as PCounter
except Exception:
    _metrics_enabled = False
    class _N:
        def __init__(self, *a, **k): pass
        def labels(self, *a, **k): return self
        def set(self, *a, **k): pass
        def inc(self, *a, **k): pass
    def start_http_server(*a, **k): pass
    Gauge = PCounter = _N

# ---------- Metrics ----------
sel_runs_total  = PCounter("pairsel_runs_total", "Aantal selector-runs")
sel_markets_g   = Gauge("pairsel_markets_selected", "Aantal geselecteerde markets")
sel_last_ok_ts  = Gauge("pairsel_last_ok_ts", "TS van laatste succesvolle selectie")
sel_errors      = PCounter("pairsel_errors_total", "Errors", ["where"])
sel_score_g     = Gauge("pairsel_market_score", "Score per market in window", ["market"])

def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()

def _start_metrics():
    global _metrics_enabled
    if not _metrics_enabled:
        print("[pairsel] Prometheus metrics uit (module niet beschikbaar).", flush=True)
        return
    try:
        start_http_server(PROM_PORT)
        print(f"[pairsel] Prometheus metrics luisteren op :{PROM_PORT}", flush=True)
    except Exception as e:
        # Poort bezet → log en ga door zonder metrics
        _metrics_enabled = False
        print(f"[pairsel] Metrics uitgeschakeld (kon poort {PROM_PORT} niet binden): {e}", file=sys.stderr)

def _connect_redis() -> Redis | None:
    try:
        r = Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception as e:
        sel_errors.labels(where="redis_connect").inc()
        print(f"[pairsel] Redis connect/ping faalde: {e}", file=sys.stderr)
        return None

def _read_recent_scores(r: Redis | None, stream: str = "signals:baseline",
                        window_sec: int = WINDOW_SEC, max_read: int = 5000) -> Counter:
    """
    Leest recente entries uit signals:baseline en telt per market.
    Geen crash als stream leeg/ontbreekt; geeft lege Counter terug.
    """
    scores = Counter()
    if r is None:
        return scores
    try:
        entries = r.xrevrange(stream, max="+", min="-", count=max_read) or []
    except Exception as e:
        sel_errors.labels(where="xrevrange").inc()
        print(f"[pairsel] xrevrange fout (ga verder met empty): {e}", file=sys.stderr)
        entries = []

    cutoff = _now_ts() - window_sec
    for xid, fields in entries:
        try:
            ts_ms = int(xid.split("-", 1)[0])
            if (ts_ms / 1000.0) < cutoff:
                continue
            m = (fields.get("market") or fields.get("pair") or fields.get("m") or "").strip()
            if not m or "-EUR" not in m:
                continue
            scores[m] += 1
        except Exception:
            # nooit crashen op vreemde records
            continue
    return scores

def _deny_set() -> set[str]:
    if not DENY_BASES_CSV:
        return set()
    return {s.strip().upper() for s in DENY_BASES_CSV.split(",") if s.strip()}

def _filter_denied_bases(scores: Counter, deny: set[str]) -> Counter:
    """Filter op base vóór '-' (case-insensitive)."""
    if not deny:
        return scores
    out = Counter()
    for m, c in scores.items():
        try:
            base = m.split("-", 1)[0].strip().upper()
        except Exception:
            base = ""
        if base in deny:
            continue
        # Safety: nooit expliciet fiat als base
        if base in {"EUR", "USD"}:
            continue
        out[m] = c
    return out

def _hysteresis_merge(prev: List[str], ranked: List[str], cap: int) -> List[str]:
    keep = [m for m in prev if m in ranked]
    for m in ranked:
        if m not in keep and len(keep) < cap:
            keep.append(m)
    return keep[:cap]

def main() -> int:
    _start_metrics()
    r = _connect_redis()
    deny = _deny_set()

    # Laad vorige selectie (optioneel, voor hysterese)
    prev: List[str] = []
    try:
        if r:
            prev = r.lrange("ai:active_markets:list", 0, -1) or []
    except Exception:
        prev = []

    while True:
        sel_runs_total.inc()

        # 1) Scores uit echte signals
        scores = _read_recent_scores(r)

        # 2) Deny-filter (geen stables/majors/fiat of wat jij definieert)
        scores = _filter_denied_bases(scores, deny)

        # 3) Rangschik met drempel (MIN_COUNT). Als echt niets voldoet: ranked blijft leeg.
        ranked = [m for m, _c in scores.most_common(100) if scores[m] >= MIN_COUNT]

        # 4) Desired ≤ MAX_CONCURRENCY met hysterese (alleen als er iets is)
        desired: List[str] = _hysteresis_merge(prev, ranked, MAX_CONCURRENCY) if ranked else []

        # 5) Metrics (scores zichtbaar maken, ongeacht write)
        try:
            if _metrics_enabled:
                for m, c in scores.items():
                    sel_score_g.labels(market=m).set(float(c))
        except Exception:
            pass

        # 6) Schrijf alleen bij non-empty desired
        if desired and r is not None:
            try:
                pipe = r.pipeline()
                pipe.delete("ai:active_markets")
                for m in desired:
                    pipe.sadd("ai:active_markets", m)
                pipe.delete("ai:active_markets:list")
                pipe.rpush("ai:active_markets:list", *desired)
                pipe.set("ai:active_markets:version", str(int(_now_ts())))
                pipe.execute()
                prev = desired
                sel_markets_g.set(len(desired))
                sel_last_ok_ts.set(_now_ts())
                print(f"[pairsel] selected = {desired}", flush=True)
            except Exception as e:
                sel_errors.labels(where="write_selection").inc()
                print(f"[pairsel] schrijven selectie faalde: {e}", file=sys.stderr)
        else:
            # Geen bruikbare selectie nu → niets schrijven, wel blijven draaien
            sel_markets_g.set(0)
            if not ranked:
                print("[pairsel] geen bruikbare signals in window; geen selectie.", flush=True)
            else:
                print("[pairsel] ranked non-empty maar desired leeg (hysterese/limiet?)", flush=True)

        time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)