# Codex → Local wijzigingen overnemen

Wanneer de Codex-omgeving geen wijzigingen naar GitHub kan pushen, kun je de actuele commit alsnog lokaal ophalen via een patch-bestand.

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

