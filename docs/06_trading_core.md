# Hoofdstuk 6 — Trading Core (dry-run → live)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Start in **dry-run** met shadow orders.
- Test guards, TP/SL, kill-switch.
- Snapshot na elke stap.

---

## Service-pakketten (blueprint-sync)
De kerncomponenten zijn nu als afzonderlijke services beschikbaar:

| Service | `main.py` | `metrics.py` | `exports` |
|---------|-----------|-------------|----------|
| trading_core | `services/trading_core/main.py` | `services/trading_core/trading_core/metrics.py` | `services/trading_core/exports/` |
| trader_signal_engine | `services/trader_signal_engine/app/main.py` | `services/trader_signal_engine/app/metrics.py` | `services/trader_signal_engine/app/exports/` |
| trader_executor | `services/trader_executor/app/main.py` | `services/trader_executor/app/metrics.py` | `services/trader_executor/app/exports/` |
| trader_pnl_orchestrator | `services/trader_pnl_orchestrator/app/main.py` | `services/trader_pnl_orchestrator/app/metrics.py` | `services/trader_pnl_orchestrator/app/exports/` |
| trader_universe_selector | `services/trader_universe_selector/app/main.py` | `services/trader_universe_selector/app/metrics.py` | `services/trader_universe_selector/app/exports/` |

> **Exports check:** `services/trading_core/trading_core/__init__.py` maakt de klassen `Decision`, `Executor`, `Metrics`,
> `Intent`, `MomentumIntent` en `MeanReversionIntent` beschikbaar zoals geëist in het bouwplan. De overige services
> publiceren hun `main`-entrypoints en eventstreams via hun respectieve `exports/__init__.py` modules.

> **Sanity check:** valideer dat alle pakketten compileren met
> `python -m compileall services/trading_core/trading_core services/trader_signal_engine/app services/trader_executor/app services/trader_pnl_orchestrator/app services/trader_universe_selector/app`.

Iedere map bevat de door het bouwplan vereiste entrypoints; bestaande scripts
(`trading_core.py`, `ai/baseline_signals.py`, metrics wrappers) importeren deze
nu rechtstreeks via het `services.*`-pad.


## 6.1 Guards & modes
- `DRY_RUN=true` verplicht initieel.
- Guards: max per-asset, max concurrent, global exposure.

### Start trading-core (venv)
```bash
sudo -u trader bash -lc '
source /srv/trading/.venv/bin/activate
export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
export DRY_RUN=true
python /srv/trading/trading_core.py
'
```
**Validatie:**
- Logs tonen dat orders alleen als shadow/dry-run worden geplaatst.
- Redis streams `orders:shadow` en `orders:signals` groeien.

### Stap-afsluiting
```bash
cat > ~/STEP-6.1-core-guards.md <<'MD'
# STEP 6.1 — Guards
- dry_run: true
- limits: <values>
MD
```

## 6.2 TP/SL & orders
- TP/SL vast of ATR; trailing optioneel.
- REST voor orders, WS voor fills/states.

### Dry-run testen (venv)
```bash
sudo -u trader bash -lc '
source /srv/trading/.venv/bin/activate
export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
export DRY_RUN=true
python /srv/trading/trading_core.py --once
'
```
**Validatie:**
- Shadow-orders verschijnen in de log zonder echte fills.
- JSONL/Parquet-logs tonen order-events in `/srv/trading/storage/parquet/<date>/trading/`.

### Stap-afsluiting
```bash
cat > ~/STEP-6.2-core-orders.md <<'MD'
# STEP 6.2 — TP/SL & orders
- strategy: <fixed/ATR/trailing>
- test orders: ok
MD
```

## 6.3 Go-Live checklist
- 24–72h data; AI-bandit nog uit.
- Kill-switch getest.

### Live-switch commando (venv)
Wanneer alle checks klaar zijn:
```bash
sudo -u trader bash -lc '
source /srv/trading/.venv/bin/activate
export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
export DRY_RUN=false
python /srv/trading/trading_core.py
'
```
> Draai alleen na expliciete go-live beslissing.

### Stap-afsluiting
```bash
cat > ~/STEP-6.3-core-golive.md <<'MD'
# STEP 6.3 — Go Live
- data window: <h>
- kill-switch: tested
MD
```
