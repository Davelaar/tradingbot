# Hoofdstuk 6 — Trading Core (dry-run → live)
**Datum:** 2025-10-29

## Hoe dit hoofdstuk door te nemen
- Start in **dry-run** met shadow orders.
- Test guards, TP/SL, kill-switch.
- Snapshot na elke stap.

---

## 6.1 Guards & modes
- `DRY_RUN=true` verplicht initieel.
- Guards: max per-asset, max concurrent, global exposure.

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

### Stap-afsluiting
```bash
cat > ~/STEP-6.3-core-golive.md <<'MD'
# STEP 6.3 — Go Live
- data window: <h>
- kill-switch: tested
MD
```
