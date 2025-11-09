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

### Stap-afsluiting
```bash
cat > ~/STEP-5.2-ai-bandit.md <<'MD'
# STEP 5.2 — Bandit hook (planned)
- status: disabled until data threshold
MD
```
