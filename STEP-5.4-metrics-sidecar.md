# STEP 5.4 — Prometheus metrics (sidecar)

## Doel
Expose 3 kernmetrics zonder sim/core aan te passen:
- trading_pnl_realized_eur_total  (Redis key: pnl:realized_eur_total)
- trading_positions_open          (Redis HLEN positions:open)
- trading_orders_outbox_len       (Redis XLEN orders:shadow)

## Bestand & Service
- /srv/trading/tools/metrics_sidecar.py
- trading-metrics.service → luistert op :9110 (/metrics)

## Health checks
- `systemctl status trading-metrics.service`
- `ss -ltnp | grep 9110`
- `curl -s localhost:9110/metrics | grep trading_`

## Troubleshooting
- Geen metrics → check Redis bereikbaar; keys bestaan; journal van service bekijken.
- Poortconflict → export `METRICS_PORT=9111` in .env.trading en herstart service.
