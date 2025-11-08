#!/usr/bin/env python3
# Trading Guard Metrics MUX
# One endpoint (:9120) that merges the Prometheus metrics exposed by all per-market
# guards. The list of guard ports is fetched from the reconciler metrics (RECON_ADDR),
# which publish lines like:
#   guard_port_assignment{market="LPT-EUR"} 9113
#
# Design goals:
#  * Fully self-contained (stdlib only).
#  * Robust against flapping guards (timeouts, missing targets).
#  * Avoid duplicate HELP/TYPE lines: keep the first occurrence per metric family.
#  * Safe concurrency with a background refresher for the port map.

import re
import time
import threading
import urllib.request
import urllib.error
from wsgiref.simple_server import make_server

# ---- Config ----
RECON_ADDR = "http://127.0.0.1:9111/metrics"   # reconciler metrics
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 9120
SCRAPE_TIMEOUT = 1.5        # seconds per guard scrape
REFRESH_MAP_EVERY = 2.0     # seconds
HEADERS = [("Content-Type", "text/plain; version=0.0.4; charset=utf-8")]

# ---- State ----
_map_lock = threading.Lock()
_portmap = {}  # market -> port (int)
_assign_re = re.compile(r'^guard_port_assignment\{market="([^"]+)"\}\s+([0-9]+(?:\.[0-9]+)?)\s*$')

# ---- Helpers ----
def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "guard-mux/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")

def _refresh_map_loop():
    global _portmap
    while True:
        try:
            text = _http_get(RECON_ADDR, timeout=SCRAPE_TIMEOUT)
            new_map = {}
            for line in text.splitlines():
                m = _assign_re.match(line.strip())
                if not m:
                    continue
                market = m.group(1)
                try:
                    port = int(float(m.group(2)))  # accept "9108" and "9108.0"
                except ValueError:
                    continue
                if port > 0:
                    new_map[market] = port
            if new_map:
                with _map_lock:
                    _portmap = new_map
        except Exception:
            # keep last good map; just try again on next tick
            pass
        time.sleep(REFRESH_MAP_EVERY)

def _merge_metrics() -> str:
    # Copy the mapping snapshot to avoid holding the lock while scraping
    with _map_lock:
        items = sorted(_portmap.items())  # [(market, port), ...]

    # If no items, expose a tiny self-metric so Prometheus scrape is still valid
    if not items:
        return (
            "# HELP guard_mux_targets Number of guard targets detected\n"
            "# TYPE guard_mux_targets gauge\n"
            "guard_mux_targets 0\n"
        )

    # For de-duplication of HELP/TYPE lines per metric family
    seen_help = set()
    seen_type = set()

    out_lines = []
    # Also export a mux metric about target count
    out_lines.append("# HELP guard_mux_targets Number of guard targets detected")
    out_lines.append("# TYPE guard_mux_targets gauge")
    out_lines.append(f"guard_mux_targets {len(items)}")

    def scrape_one(market: str, port: int):
        url = f"http://127.0.0.1:{port}/metrics"
        try:
            text = _http_get(url, timeout=SCRAPE_TIMEOUT)
        except Exception:
            # On error, record a mux error metric for visibility
            out_lines.append(f'guard_mux_scrape_errors_total{{market="{market}"}} 1')
            return

        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            # Filter HELP/TYPE to keep only first per-family occurrence
            if line.startswith("# HELP "):
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    name = parts[2]
                    if name in seen_help:
                        continue
                    seen_help.add(name)
            elif line.startswith("# TYPE "):
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    name = parts[2]
                    if name in seen_type:
                        continue
                    seen_type.add(name)
            out_lines.append(line)

    threads = []
    for market, port in items:
        t = threading.Thread(target=scrape_one, args=(market, port), daemon=True)
        threads.append(t)
        t.start()

    # Join with a global ceiling so we never block too long
    deadline = time.time() + SCRAPE_TIMEOUT + 0.5
    for t in threads:
        remaining = deadline - time.time()
        if remaining > 0:
            t.join(remaining)

    return "\n".join(out_lines) + "\n"

# ---- WSGI app ----
def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path == "/metrics":
        body = _merge_metrics().encode("utf-8")
        start_response("200 OK", [*HEADERS, ("Content-Length", str(len(body)))])
        return [body]
    elif path in ("/", "/-/ready", "/healthz"):
        # Minimal readiness page
        with _map_lock:
            cnt = len(_portmap)
        body = f"OK targets={cnt}\n".encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
        return [body]
    else:
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found\n"]

def main():
    threading.Thread(target=_refresh_map_loop, daemon=True).start()
    httpd = make_server(LISTEN_HOST, LISTEN_PORT, app)
    print(f"[guard-mux] listening on {LISTEN_HOST}:{LISTEN_PORT} — recon={RECON_ADDR}", flush=True)
    httpd.serve_forever()

if __name__ == "__main__":
    main()
