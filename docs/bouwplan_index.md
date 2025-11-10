
# Bouwplan Index — Tradingbot Bitvavo (v2)
**Datum:** 2025-10-29

Deze index hoort bij de 9 hoofdstukken van het bouwplan.
Gebruik dit bestand als **startpunt en voortgangscheck**.

---

## 🔧 Doel
Een volledig herhaalbare installatie van de tradingbot op **Ubuntu 24.04** — van kale server tot productieomgeving.

- Ingest via **Bitvavo SDK (WebSocket)**  
- Data → **Redis Streams → Parquet**  
- AI-laag (rule + bandit hooks)  
- Trading Core (dry-run → live)  
- Grafische filemanager (upload/zip/unzip)  
- Observability (Prometheus + Grafana)

---

## 📂 Hoofdstukken — volgorde en voortgang

| Nr | Bestand | Beschrijving | Status |
|----|----------|---------------|---------|
| 1 | 01_server.md | Serverfundament, gebruiker `trader`, Docker, firewall | ☐ niet gestart / ☑ klaar |
| 2 | 02_weblayer.md | Weblaag (Caddy of Nginx) + FileBrowser | ☐ / ☑ |
| 3 | 03_data_layer.md | Redis + opslagpaden | ☐ / ☑ |
| 4 | 04_python_ingest.md | Python 3.12, Bitvavo-ingest (WS→Redis→Parquet) | ☐ / ☑ |
| 5 | 05_ai_layer.md | AI baseline + bandit hooks | ☐ / ☑ |
| 6 | 06_trading_core.md | Trading Core (dry-run→live) | ☐ / ☑ |
| 7 | 07_observability.md | Prometheus + Grafana | ☐ / ☑ |
| 8 | 07_go_live_checklist.md | Eindcontrole ingest→core→executor live | ☐ / ☑ |
| 9 | 08_lifecycle.md | Backups, updates, security | ☐ / ☑ |

Markeer de kolom **Status** per stap met ☑ zodra een hoofdstuk volledig gevalideerd is.

---

## 🧭 Gebruik en snapshot‑routine
1. Voer per hoofdstuk de stappen exact uit.  
2. Na iedere stap maak je een `STEP-x.x-description.md` (zoals in elk hoofdstuk beschreven).  
3. Upload deze snapshot‑MD’s in je projectmap of repository — zo kan elke volgende fase hierop voortbouwen.  
4. Bij herinstallatie hoef je alleen dit bouwplan + snapshots te uploaden.

---

## 🚀 Herstart of herstel
Na verwijdering van een chat hoef je alleen opnieuw te uploaden:
```
01_server.md
02_weblayer.md
03_data_layer.md
04_python_ingest.md
05_ai_layer.md
06_trading_core.md
07_observability.md
07_go_live_checklist.md
08_lifecycle.md
bouwplan_index.md
```
Daarna kan ik direct verder waar we gebleven waren.

---

**Einde Index (v2)**  
