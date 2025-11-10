# Hoofdstuk 7 — Go-live checklist

Dit hoofdstuk vat alle stappen samen die nodig zijn om van een volledig voorbereide
ontwikkelomgeving naar een live draaiende trading stack te gaan. Gebruik het als
laatste controlelijst nadat Hoofdstuk 4 t/m 6 zijn afgerond.

## 7.1 Basisvoorwaarden

1. **Codebase**
   - Laatste commit binnen op `main` of feature branch (`git status` schoon).
   - `python -m compileall` op de services-pakketten draait zonder fouten
     (zie §7.4).

2. **Python omgeving**
   - `/srv/trading/.venv` bestaat en bevat de vereiste packages:
     `python-bitvavo-api`, `redis`, `orjson`, `pyarrow`, `prometheus-client`.
   - `.venv` geactiveerd in shells of opgenomen in systemd-units.

3. **Configuratie & secrets**
   - `/srv/trading/.env.bitvavo` bevat geldige Bitvavo API key & secret.
   - Permissies strikt (600) en owner `trader:trader`.
   - Redis endpoint (`REDIS_URL`) en opslagpad (`PARQUET_DIR`) bereikbaar.

4. **Infra-services**
   - Redis server actief (`systemctl status redis` of Docker).
   - Prometheus/Grafana klaar voor metrics-scrape (optioneel maar aanbevolen).
   - Filesystem `/srv/trading/storage/parquet` met voldoende ruimte & rechten.

## 7.2 Realtime ingest-laag

Alle ingest-processen moeten draaien vóórdat signaal- of trading-services
worden gestart. Start ze vanuit de venv en laad `.env.bitvavo`.

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python /srv/trading/ingest.py
'
```

- **Valideer:** `redis-cli XLEN bitvavo:ticker24h` groeit; onder
  `${PARQUET_DIR}/YYYY-MM-DD/` ontstaan JSONL/Parquet-bestanden.

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python /srv/trading/ingest_orderbook.py
'
```

- **Valideer:** Streams `bitvavo:book` en `bitvavo:orderbook:update` groeien;
  JSONL in submappen `orderbook/top`, `orderbook/update`, `orderbook/snapshot`.

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python /srv/trading/ingest_candles.py
'
```

- **Valideer:** Per interval (`bitvavo:candles:1m`, etc.) groeit Redis stream;
  Parquet/JSONL onder `candles/<interval>/` aanwezig.

> **Tip:** draai ingest-processen in `tmux` of systemd units (zie `systemd/`).

## 7.3 Services & microservices

### 7.3.1 Signal engine

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python -m services.trader_signal_engine.app
'
```

- **Config:** `SIGNAL_STREAM` (default `signals:baseline`), spread/volatility
  thresholds via env vars.
- **Valideer:** `redis-cli XLEN signals:baseline` groeit; Prometheus endpoint
  op `:9601/metrics` (standaard) geeft `trader_signal_engine_*` metrics.

### 7.3.2 Trading core (dry-run → live)

1. **Dry-run controle**

   ```bash
   sudo -u trader bash -lc '
     source /srv/trading/.venv/bin/activate
     export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
     export DRY_RUN=true
     python -m services.trading_core
   '
   ```

   - Bevestig dat nieuwe intenties in `orders:shadow` verschijnen.
   - `trading:events` bevat guard logs (`kill_switch`, exposure checks).

2. **Live-modus**

   - Zorg dat `trader_executor` service klaarstaat (zie §7.3.3).
   - Zet `DRY_RUN=false` en controleer exposures (`MAX_*` env vars).
   - Activeer live run pas na go/no-go.

### 7.3.3 Trader executor

Verwerkt `orders:shadow` naar daadwerkelijke Bitvavo orders (of
naar geconfigureerde stub in huidige implementatie).

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python -m services.trader_executor.app
'
```

- **Valideer:** Redis stream `orders:executed` groeit; Prometheus endpoint
  rapporteert `trader_executor_*` counters.
- **Kill switch:** `trading:kill=1` blokkeert nieuwe executies.

### 7.3.4 PnL orchestrator

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python -m services.trader_pnl_orchestrator.app
'
```

- Verwerkt `orders:executed` & `trading:events` naar PnL snapshots.
- Metrics: `trader_pnl_*` counters en gauges, Redis hash `trading:pnl`.

### 7.3.5 Universe selector

```bash
sudo -u trader bash -lc '
  source /srv/trading/.venv/bin/activate
  export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
  python -m services.trader_universe_selector.app
'
```

- Schrijft geselecteerde markten naar `trading:universe` hash / Redis set.
- Controleer dat ingest scripts markt-keuze respecteren (`INGEST_MARKETS`).

## 7.4 Sanity checks vóór go-live

1. **Compile check**

   ```bash
   python -m compileall \
     services/trading_core/trading_core \
     services/trader_signal_engine/app \
     services/trader_executor/app \
     services/trader_pnl_orchestrator/app \
     services/trader_universe_selector/app
   ```

2. **Redis gezondheid**
   - `redis-cli INFO memory | grep used_memory_human` < 70% van RAM.
   - `redis-cli MONITOR` (tijdelijk) toont verkeer; geen errors.

3. **Prometheus scrape**
   - `curl http://localhost:9601/metrics` (pas poorten aan per service).
   - Alerts ingesteld voor `kill_switch=ON`, hoge error-rates, etc.

4. **Filesystem**
   - Dagelijkse rotatie van JSONL/Parquet via cron of logrotate.
   - Backups / rsync naar veilige opslag.

5. **Failover & herstel**
   - Procedure bekend voor herstart van services.
   - `systemd/` units geactiveerd (`systemctl enable trading-ingest@...`).

## 7.5 Live checklist (go/no-go)

| Stap | Vraag | Status |
|------|-------|--------|
| 1 | Ingest streams actief en up-to-date? | ☐ |
| 2 | Signal engine produceert intenties? | ☐ |
| 3 | Trading core in dry-run gevalideerd? | ☐ |
| 4 | Kill switch getest (blokkeert orders)? | ☐ |
| 5 | Executor en PnL orchestrator draaien? | ☐ |
| 6 | Prometheus & Grafana dashboards groen? | ☐ |
| 7 | Team akkoord voor live-switch? | ☐ |
| 8 | `DRY_RUN=false` en kill switch op OFF? | ☐ |

Documenteer de beslissingen in `~/STEP-7.5-go-live.md` met datum, betrokken
personen en eventuele risico’s.

## 7.6 Post-go-live observatie

- Volg Redis streams (laatste events) en metrics dashboards gedurende de
  eerste uren.
- Zet alerts voor `orders:shadow`-backlog, error tellers en PnL outliers.
- Dagelijkse controle van Parquet output + backups.
- Houd logboeken bij (`journalctl -u trading-*` of tmux logs).

Met deze checklist kun je gefaseerd en gecontroleerd naar productie gaan.
Zodra alle vakjes zijn afgevinkt, staat de stack live conform het bouwplan.
