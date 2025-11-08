#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Guard Reconciler
- Leest gewenste markten uit Redis (SET: ai:active_markets, LIST: ai:active_markets:list -> volgorde)
- Past deny-lijst toe (majors/stables/fiat eruit)
- Handhaaft max concurrency (GUARD_MAX_CONCURRENCY)
- Schrijft per market een EnvironmentFile met unieke PROM_PORT naar /etc/trading/guard/<MARKET>.env
- Start/stop/restart templated guards: trading-guard@<MARKET>.service
- Exporteert metrics op RECONCILER_PROM_PORT (default 9111)

Veiligheden:
- Robuuste parsing van systemd output (alleen 'trading-guard@*.service', en skip zonder '@' of '.service')
- Unieke poort-allocatie met _is_port_free(); geen poortconflicten
- Metrics-bind is best-effort (faalt niet de hoofdloop)
"""

from __future__ import annotations
import os
import sys
import time
import subprocess
from typing import List, Tuple, Optional

# ---------- Config via ENV ----------
REDIS_URL       = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_CONCURRENCY = int(os.getenv("GUARD_MAX_CONCURRENCY", "5"))
PROM_BASE       = int(os.getenv("GUARD_PROM_BASE", "9105"))
PROM_RANGE      = int(os.getenv("GUARD_PROM_RANGE", "50"))
PROM_PORT       = int(os.getenv("RECONCILER_PROM_PORT", "9111"))
DENY_BASES_CSV  = os.getenv("GUARD_DENY_BASES", "BTC,ETH,BNB,ADA,SOL,XRP,USDT,USDC,EUR,USD,DAI,TUSD,FDUSD,EURS,USDE")
LOOP_SLEEP_SEC  = float(os.getenv("LOOP_SLEEP_SEC", "3"))

ENV_DIR = "/etc/trading/guard"

# ---------- Dependencies ----------
try:
    from redis import Redis
except Exception as e:
    print(f"[recon] FATAL: python-redis ontbreekt: {e}", file=sys.stderr)
    sys.exit(1)

# Metrics (best-effort)
_metrics_enabled = True
try:
    from prometheus_client import start_http_server, Gauge, Counter
except Exception:
    _metrics_enabled = False
    class _N:
        def __init__(self, *a, **k): pass
        def labels(self, *a, **k): return self
        def set(self, *a, **k): pass
        def inc(self, *a, **k): pass
    def start_http_server(*a, **k): pass
    Gauge = Counter = _N

recon_runs   = Counter("guard_reconcile_runs_total", "Aantal reconcile-loops")
recon_errors = Counter("guard_reconcile_errors_total", "Fouten", ["stage"])
active_g     = Gauge("guard_active_markets", "Aantal actieve guard-instances")
port_g       = Gauge("guard_port_assignment", "Poort per market", ["market"])
last_ok_ts   = Gauge("guard_reconcile_last_ok_ts", "Epoch TS laatste succesvolle reconcile")

def _start_metrics() -> None:
    global _metrics_enabled
    if not _metrics_enabled:
        print("[recon] Metrics uit (prometheus_client niet beschikbaar).", flush=True)
        return
    try:
        start_http_server(PROM_PORT)
        print(f"[recon] Metrics luisteren op :{PROM_PORT}", flush=True)
    except Exception as e:
        _metrics_enabled = False
        print(f"[recon] Metrics uitgeschakeld (kan poort {PROM_PORT} niet binden): {e}", file=sys.stderr)

def _connect_redis() -> Optional[Redis]:
    try:
        r = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        r.ping()
        return r
    except Exception as e:
        recon_errors.labels(stage="redis_connect").inc()
        print(f"[recon] Redis connect/ping faalde: {e}", file=sys.stderr)
        return None

def _deny_set() -> set[str]:
    if not DENY_BASES_CSV:
        return set()
    return {s.strip().upper() for s in DENY_BASES_CSV.split(",") if s.strip()}

def _filter_denied(markets: List[str], deny: set[str]) -> List[str]:
    if not deny:
        return markets
    out: List[str] = []
    for m in markets:
        try:
            base = m.split("-", 1)[0].strip().upper()
        except Exception:
            base = ""
        if (not base) or (base in deny) or (base in {"EUR", "USD"}):
            continue
        out.append(m)
    return out

def _sysrun(cmd: List[str]) -> Tuple[int, str]:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output

def _list_running_instances() -> List[str]:
    """
    Robuust: vraag systemd alleen 'trading-guard@*.service'.
    Parse uitsluitend de 1e kolom (unitnaam). Skip alles zonder '@' of zonder '.service'.
    """
    rc, out = _sysrun([
        "systemctl", "list-units", "trading-guard@*.service",
        "--type=service", "--all", "--no-legend", "--plain"
    ])
    if rc != 0 or not out:
        return []
    instances: List[str] = []
    for line in out.splitlines():
        line = (line or "").strip()
        if not line:
            continue
        unit = line.split()[0] if line.split() else ""
        # alleen exacte templated units accepteren
        if not unit or "@" not in unit or not unit.endswith(".service"):
            continue
        if not unit.startswith("trading-guard@"):
            continue
        try:
            inst = unit.split("@", 1)[1].rsplit(".service", 1)[0]
        except Exception:
            continue
        if inst:
            instances.append(inst)
    return instances

def _is_port_free(port: int) -> bool:
    import socket
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass

def _read_current_port(market: str) -> Optional[int]:
    path = os.path.join(ENV_DIR, f"{market}.env")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            for line in f:
                line = (line or "").strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("PROM_PORT="):
                    return int(line.split("=", 1)[1])
    except Exception:
        return None
    return None

def _write_env(market: str, port: int) -> str:
    os.makedirs(ENV_DIR, exist_ok=True)
    path = os.path.join(ENV_DIR, f"{market}.env")
    with open(path, "w") as f:
        f.write(f"PROM_PORT={port}\n")
    return path

def _assign_ports(desired: List[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    used: set[int] = set()
    p = PROM_BASE
    for m in desired:
        hops = 0
        # kies eerst bestaande port als die vrij is, anders alloceren
        cur = _read_current_port(m)
        candidate = cur if (cur is not None and _is_port_free(cur) and cur not in used) else p
        while (candidate in used) or (not _is_port_free(candidate)):
            candidate += 1
            hops += 1
            if hops > PROM_RANGE:
                candidate += 1
                if hops > PROM_RANGE + 512:
                    recon_errors.labels(stage="port_assign_exhausted").inc()
                    break
        mapping[m] = candidate
        used.add(candidate)
        # volgende startpositie schuift lineair door
        p = max(p, candidate + 1)
    return mapping

def _start_instance(market: str) -> None:
    _sysrun(["systemctl", "enable", "--now", f"trading-guard@{market}.service"])

def _restart_instance(market: str) -> None:
    _sysrun(["systemctl", "restart", f"trading-guard@{market}.service"])

def _stop_instance(market: str) -> None:
    _sysrun(["systemctl", "disable", "--now", f"trading-guard@{market}.service"])

def _ordered_desired(r: Optional[Redis]) -> List[str]:
    desired: List[str] = []
    try:
        if r:
            lst = r.lrange("ai:active_markets:list", 0, -1)
            st  = list(r.smembers("ai:active_markets") or [])
            if lst:
                st_set = set(st)
                desired = [m for m in lst if m in st_set]
            else:
                desired = sorted(st)
    except Exception as e:
        recon_errors.labels(stage="read_ai_markets").inc()
        print(f"[recon] Fout lezen ai:active_markets: {e}", file=sys.stderr)
        desired = []
    return desired

def main() -> int:
    from time import time as _now
    _start_metrics()
    r = _connect_redis()
    deny = _deny_set()

    while True:
        recon_runs.inc()
        try:
            desired_all = _ordered_desired(r)
            desired = _filter_denied(desired_all, deny)[:MAX_CONCURRENCY]

            running = _list_running_instances()

            # stop wat niet meer gewenst is
            for inst in running:
                if inst not in desired:
                    _stop_instance(inst)

            # poort toewijzen
            port_map = _assign_ports(desired)

            # env schrijven en (re)starten
            for m in desired:
                want = port_map.get(m, PROM_BASE)
                have = _read_current_port(m)
                if have != want:
                    _write_env(m, want)
                    if m in running:
                        _restart_instance(m)
                    else:
                        _start_instance(m)
                else:
                    if m not in running:
                        _start_instance(m)

            # status naar Redis (lijst met actieve instances)
            current = _list_running_instances()
            if r:
                try:
                    pipe = r.pipeline()
                    pipe.delete("guard:active_markets")
                    if current:
                        pipe.rpush("guard:active_markets", *current)
                    pipe.execute()
                except Exception as e:
                    recon_errors.labels(stage="write_status").inc()
                    print(f"[recon] Redis status schrijf-fout: {e}", file=sys.stderr)

            # metrics
            try:
                active_g.set(len(current))
                for m, p in port_map.items():
                    port_g.labels(market=m).set(float(p))
                last_ok_ts.set(_now())
            except Exception:
                pass

        except Exception as e:
            recon_errors.labels(stage="main_loop").inc()
            print(f"[recon] Unhandled error: {e}", file=sys.stderr)

        time.sleep(LOOP_SLEEP_SEC)

    # onbereikbaar
    # return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"[recon] Fatal: {e}", file=sys.stderr)
        sys.exit(1)