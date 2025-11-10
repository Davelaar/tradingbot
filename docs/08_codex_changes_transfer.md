# Codex → Local wijzigingen overnemen

Wanneer de Codex-omgeving geen wijzigingen naar GitHub kan pushen, kun je de actuele commit alsnog lokaal ophalen via een patch-
bestand of volledige tarball. Onderstaande stappen werken voor iedere branch.

## 1. Patch genereren in Codex

```bash
# vanuit /workspace/tradingbot
LATEST_COMMIT="$(git rev-parse HEAD)"
git format-patch -1 "$LATEST_COMMIT" --stdout > /tmp/codex_latest.patch
ls -lh /tmp/codex_latest.patch
```

Download vervolgens `/tmp/codex_latest.patch` via de Codex-bestandsbrowser.

## 2. Patch toepassen op je lokale repository

```bash
cd /pad/naar/jouw/tradingbot
curl -o /tmp/codex_latest.patch "<UPLOAD_URL_OF_PATCH>"
# of verplaats het bestand handmatig naar je machine

git apply /tmp/codex_latest.patch
# controleer: git status
```

Mocht Git conflicten detecteren, los die dan handmatig op en voer daarna `git add` uit voor de aangepaste bestanden.

### Veelvoorkomende merge-conflict: `ai/baseline_signals.py`
De Codex-branch zet de volledige logica van de baseline-signaallaag in
`services/trader_signal_engine/app/main.py`. Het top-level script
`ai/baseline_signals.py` is daardoor een dunne wrapper die alleen
`pump()` vanuit de service aanroept. Kies bij een conflict daarom voor
de variant uit Codex (wrapper) of verwijder de oude inline logica
handmatig, zodat alle code vanuit `services/trader_signal_engine/app`
wordt geladen.

Sluit daarna af met:

```bash
git add ai/baseline_signals.py services/trader_signal_engine/app/main.py
```

## 3. Committen en pushen

```bash
git commit -am "Apply Codex patch"
git push origin <jouw-branch>
```

Daarna kun je, indien nodig, een pull request openen of `main` bijwerken.

## Alternatief: tarball van de volledige boom

Wil je liever de volledige projectmap downloaden?

```bash
cd /workspace
tar -czf /tmp/tradingbot_codex.tar.gz tradingbot
```

Download `tradingbot_codex.tar.gz`, pak het lokaal uit en vergelijk/merge met je eigen repo.

