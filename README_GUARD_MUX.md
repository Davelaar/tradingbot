# Guard Metrics MUX (:9120)

Eén endpoint dat alle guard-metrics samenvoegt. De lijst van guard-poorten
komt uit de reconciler-metrics (`guard_port_assignment{market="..."} <port>`).

## Files
- `/srv/trading/tools/guard_mux_exporter.py`
- `/etc/systemd/system/trading-guard-mux.service`
- Prometheus static target (als je Prometheus in Docker draait):
  copy `guard_mux.yml` naar `/etc/prometheus/targets/` in de container en reload.

## Installatie (host)
```bash
install -d -m 0755 /srv/trading/tools
install -m 0755 guard_mux_exporter.py /srv/trading/tools/guard_mux_exporter.py
install -m 0644 trading-guard-mux.service /etc/systemd/system/trading-guard-mux.service
systemctl daemon-reload
systemctl enable --now trading-guard-mux.service
curl -s http://127.0.0.1:9120/metrics | head
```

## Prometheus (container)
```bash
docker cp guard_mux.yml observability-prometheus-1:/etc/prometheus/targets/guard_mux.yml
# Reload Prometheus (HTTP) of via SIGHUP:
docker kill -s HUP observability-prometheus-1
```

## Verwacht gedrag
- `/metrics` antwoord in ~1–2 seconden, ook als een deel van de guards down is.
- Duplicated HELP/TYPE worden gefilterd; samples blijven intact.
- Readiness op `/-/ready` geeft `OK targets=<n>`.
