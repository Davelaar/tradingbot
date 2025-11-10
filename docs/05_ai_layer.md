# Hoofdstuk 5 — AI-laag (baseline + bandit hooks)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Eerst alleen **baseline** regels activeren (zonder leercomponent).
- Bandit pas inschakelen bij voldoende data.
- Snapshot na elke stap.

---

## 5.1 Baseline rules
- Filters: spread, volatility, recent volume spike, wick ratio.
- Output: **signal stream** (Redis key `signals:baseline`).

### Run baseline (venv)
> Het baseline-script staat nu onder `services/trader_signal_engine/app/main.py`;
  het legacy-pad `ai/baseline_signals.py` importeert dit service-entrypoint.

```bash
sudo -u trader bash -lc '
source /srv/trading/.venv/bin/activate
export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
python /srv/trading/ai/baseline_signals.py
'
```
**Validatie:**
- Redis stream `signals:baseline` groeit (`redis-cli xlen signals:baseline`).
- Logs tonen gefilterde signalen per markt en spread-checks.

### Stap-afsluiting
```bash
cat > ~/STEP-5.1-ai-baseline.md <<'MD'
# STEP 5.1 — AI baseline
- filters enabled: yes
- signal key: signals:baseline
MD
```

## 5.2 Bandit hook (later)
- Contextual bandit (LinUCB/Thompson); reward = PnL/drawdown.
- Governance: exploration cap; fail-safe circuit breaker.

### Voorbereiding (venv)
Laat de bandit pas draaien na voldoende data, maar zorg nu alvast voor een venv-commando:
```bash
sudo -u trader bash -lc '
source /srv/trading/.venv/bin/activate
export $(grep -v "^#" /srv/trading/.env.bitvavo | xargs -d "\n")
python /srv/trading/ai/bandit_hook.py
'
```
> Stop direct (`Ctrl+C`) zodra je hebt gevalideerd dat dependencies laden.

### Stap-afsluiting
```bash
cat > ~/STEP-5.2-ai-bandit.md <<'MD'
# STEP 5.2 — Bandit hook (planned)
- status: disabled until data threshold
MD
```
