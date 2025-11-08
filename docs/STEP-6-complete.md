# STEP 6 — Fills Sim & Metrics (COMPLEET)

## 🎯 Doel
De *fills simulator* verwerkt orders uit `orders:shadow`, volgt candles, en simuleert **TP / SL / trailing-stop**-fills
met realistische **Bitvavo-fees**, **latency/jitter**, en exporteert kernwaarden via een **metrics sidecar** voor Prometheus.
Resultaten naar Redis:
- `positions:open`, `positions:closed`
- `pnl:realized_eur_total`, `pnl:realized_eur:<market>`
- events in `trading:events`

---

## 📦 Overzicht Services & Bestanden

| Service                         | Bestand                                        | Doel |
|---------------------------------|------------------------------------------------|------|
| `trading-sim.service`           | `/srv/trading/tools/fills_sim.py`              | Dry‑run fills + PnL (TP/SL/Trail, fees, latency) |
| `trading-fees-sync.service`     | `/srv/trading/tools/fees_sync_bitvavo.py`      | (Optioneel) Echte Bitvavo-fees ophalen (WS auth) |
| `trading-metrics.service`       | `/srv/trading/tools/metrics_sidecar.py`        | Expose `trading_*` metrics op `:9110` |

> **Let op:** Je draait alles al werkend met versies:
> - `fills_sim v3` (trail + latency/jitter + fees)
> - `metrics_sidecar` actief en luistert op `:9110`
> - `fees_sync` kan draaien met geldige API-sleutels (nu fallback 15/25 bps aanwezig in Redis)

---

## ⚙️ Vereisten (samenvatting)

- **Python venv**: `/srv/trading/.venv`
- **Redis** bereikbaar op `REDIS_URL` (standaard `redis://127.0.0.1:6379/0`)
- **Streams & groepen**
  - `orders:shadow` (group: `sim_orders`)
  - `candles:1m`   (group: `sim_candles`)
- **Fees** in Redis (fallback of via sync):
  - `fees:account:maker_bps` (bijv. `15`)
  - `fees:account:taker_bps` (bijv. `25`)

---

## 🧩 Config keys (in `/srv/trading/.env.trading`)

Essentieel:
```
REDIS_URL=redis://127.0.0.1:6379/0
SLOTS=5
# (optioneel) Bitvavo API (alleen nodig voor fees_sync)
BITVAVO_REST_URL=https://api.bitvavo.com/v2
BITVAVO_WS_URL=wss://ws.bitvavo.com/v2/
BITVAVO_APIKEY=
BITVAVO_SECRET=
```

Simulator leest uitsluitend uit Redis en streams; **geen** echte orders.

---

## 🚀 Start/Herstart services

### trading-sim.service
```
sudo systemctl restart trading-sim.service
journalctl -u trading-sim.service -n 40 --no-pager -l
```

### trading-fees-sync.service (optioneel)
```
sudo systemctl restart trading-fees-sync.service
journalctl -u trading-fees-sync.service -n 40 --no-pager -l
```

### trading-metrics.service
```
sudo systemctl restart trading-metrics.service
journalctl -u trading-metrics.service -n 20 --no-pager
```

---

## ✅ Validaties (moeten slagen)

### 1) Redis groups & backlog
```
docker run --rm --network host redis:7-alpine redis-cli XINFO GROUPS orders:shadow
docker run --rm --network host redis:7-alpine redis-cli XINFO GROUPS candles:1m
docker run --rm --network host redis:7-alpine redis-cli XPENDING orders:shadow  sim_orders - + 10
docker run --rm --network host redis:7-alpine redis-cli XPENDING candles:1m    sim_candles - + 10
```
**Verwacht:** `pending=0`, geldige `last-delivered-id`.

### 2) Fees aanwezig
```
docker run --rm --network host redis:7-alpine redis-cli \
  MGET fees:account:maker_bps fees:account:taker_bps
```
**Verwacht:** `15` en `25` (of live waarden via fees_sync).

### 3) Metrics sidecar luistert
```
ss -ltnp | grep 9110 || echo "no listener on 9110"
curl -s localhost:9110/metrics | grep -E 'trading_(pnl_realized_eur_total|positions_open|orders_outbox_len)'
```
**Verwacht:** 3 regels met `trading_*` metrics.

### 4) End‑to‑end sim TP (maker-sell) en SL (taker-sell)

**TP‑pad (TP raakt eerder dan SL)**
```
SB=$(docker run --rm --network host redis:7-alpine redis-cli GET account:slot_budget_eur)

docker run --rm --network host redis:7-alpine redis-cli XADD orders:shadow "*" \
  action OPEN market SIMTP-OK-EUR side buy price 100 size_eur "$SB" \
  tp_pct 0.0060 sl_pct 0.0040 mode fixed signal_id SIMTP-OK-1 dry true

docker run --rm --network host redis:7-alpine redis-cli XADD candles:1m "*" \
  market SIMTP-OK-EUR o 100 h 100.8 l 100.1 c 100.4

docker run --rm --network host redis:7-alpine redis-cli HGET positions:closed SIMTP-OK-EUR:SIMTP-OK-1
```
**Verwacht:** JSON met `reason:"TP"`, `sell_fee_eur` ≈ maker‑fee.

**SL‑pad (SL onder low)**
```
SB=$(docker run --rm --network host redis:7-alpine redis-cli GET account:slot_budget_eur)

docker run --rm --network host redis:7-alpine redis-cli XADD orders:shadow "*" \
  action OPEN market SIMSL-OK-EUR side buy price 100 size_eur "$SB" \
  tp_pct 0.0060 sl_pct 0.0040 mode fixed signal_id SIMSL-OK-1 dry true

docker run --rm --network host redis:7-alpine redis-cli XADD candles:1m "*" \
  market SIMSL-OK-EUR o 100 h 100.2 l 99.5 c 99.8

docker run --rm --network host redis:7-alpine redis-cli HGET positions:closed SIMSL-OK-EUR:SIMSL-OK-1
```
**Verwacht:** `reason:"SL"`, `sell_fee_eur` ≈ taker‑fee.

### 5) Trailing‑stop scenario
```
SB=$(docker run --rm --network host redis:7-alpine redis-cli GET account:slot_budget_eur)

docker run --rm --network host redis:7-alpine redis-cli XADD orders:shadow "*" \
  action OPEN market TRTEST-OK-EUR side buy price 100 size_eur "$SB" \
  tp_pct 0.0060 sl_pct 0.0040 trail_pct 0.0060 mode fixed signal_id TRTEST-OK-1 dry true

docker run --rm --network host redis:7-alpine redis-cli XADD candles:1m "*" \
  market TRTEST-OK-EUR o 100 h 101.0 l 100.3 c 100.5

docker run --rm --network host redis:7-alpine redis-cli HGET positions:closed TRTEST-OK-EUR:TRTEST-OK-1
```
**Verwacht:** `trail_active:true`, `reason:"TRAIL"` met exit rond trail‑stop.

---

## 🧪 Interpretatie van PnL met fees & latency

- **Buy fee**: taker (default) — sim verrekent `size_eur * taker_bps / 10_000`
- **Sell fee**:
  - **TP**: maker (limiet op target) — `maker_bps`
  - **SL/Trail**: doorgaans taker op marktprijs — `taker_bps`
- **Latency/jitter**: sim gebruikt ms‑jitter → lichte afwijking t.o.v. perfecte fill → realistischer PnL.

Totaal PnL wordt geaccumuleerd in:
- `pnl:realized_eur_total`
- `pnl:realized_eur:<market>`

---

## 🔧 Troubleshooting (snel)

- **`NOGROUP … XREADGROUP`**  
  → Maak (of reset) consumer groups:
  ```
  docker run --rm --network host redis:7-alpine redis-cli \
    XGROUP CREATE orders:shadow sim_orders 0-0 MKSTREAM 2>/dev/null || true
  docker run --rm --network host redis:7-alpine redis-cli \
    XGROUP CREATE candles:1m   sim_candles 0-0 MKSTREAM 2>/dev/null || true
  ```

- **Geen metrics op `:9110`**  
  - Check service: `systemctl status trading-metrics.service`  
  - Listener: `ss -ltnp | grep 9110`  
  - Curl: `curl -s localhost:9110/metrics | head`

- **Geen fees in Redis**  
  - Fallback setten:
    ```
    docker run --rm --network host redis:7-alpine redis-cli \
      MSET fees:account:maker_bps 15 fees:account:taker_bps 25
    ```
  - Of `trading-fees-sync.service` met geldige API keys draaien.

---

## 🧾 Wat er NU draait (samengevat)

- **Core**: dyn‑cap, slots OK; orders naar `orders:shadow`
- **Sim**: v3 actief; TP/SL/Trail + fees + latency
- **Metrics sidecar**: luistert op `:9110`, exporteert gauges:
  - `trading_pnl_realized_eur_total`
  - `trading_positions_open`
  - `trading_orders_outbox_len`

---

## ▶️ Door naar **STEP 7 — Grafana/Prometheus** (voorbereiding)

Benodigd als input:
- Metrics endpoint: `http://<host>:9110/metrics`
- Redis Grafana panels (optioneel)
- Dashboards:
  - PnL (total + per market)
  - Open positions count
  - Orders outbox length
  - Event feed (uit `trading:events` via Redis datasource of loki alternatief)

We houden het strikt stap‑voor‑stap:
1. Prometheus container + scrape job voor `:9110`
2. Grafana + dashboard JSON import
3. Validaties per panel + alerting‑basis (optioneel)

**Einde STEP 6 ✅** — alles is verifieerbaar en live.
