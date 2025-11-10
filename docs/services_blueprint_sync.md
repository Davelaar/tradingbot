# Services Blueprint Synchronisatiegids

Deze gids legt uit hoe je de service-herstructureringscommit uit de Codex-omgeving
kunt ophalen en samenvoegen met jouw lokale wijzigingen, zodat alles uiteindelijk
naar GitHub kan worden gepusht.

## 1. Haal de Codex-commit als patch op

Open in de Codex-container een shell en voer het volgende uit om de commit
`d7329aa296ac99ef2706deae8465c67a0f13e951` ("Align services packages with
blueprint layout") als patchbestand te exporteren:

```bash
git format-patch -1 d7329aa296ac99ef2706deae8465c67a0f13e951 --stdout > /tmp/services_blueprint.patch
```

Kopieer daarna `/tmp/services_blueprint.patch` naar je lokale machine (bijv.
via de downloadfunctie van het platform of `scp`).

## 2. Pas de patch lokaal toe

Zorg dat je lokale repository schoon is (commit of stash je eigen werk) en voer
dan uit:

```bash
cd /pad/naar/tradingbot
git apply /pad/naar/services_blueprint.patch
# of gebruik git am als je de commit inclusief metadata wilt behouden:
# git am /pad/naar/services_blueprint.patch
```

Controleer de wijzigingen:

```bash
git status
git diff --stat
```

## 3. Los eventuele conflicten op

Als `git apply` conflicten meldt, los die dan handmatig op en markeer de
bestanden als opgelost:

```bash
git add <bestand1> <bestand2> ...
```

Maak daarna zelf een commit (of laat `git am` de originele commit gebruiken).

## 4. Voeg je eigen lokale wijzigingen toe

Voeg nu je eigen lokale aanpassingen weer toe en commit ze bovenop de
services-blueprint commit. Controleer het log:

```bash
git log --oneline -5
```

## 5. Push naar GitHub (via HTTPS + PAT)

Omdat SSH vanuit deze omgeving geblokkeerd is, push je lokaal via HTTPS met je
persoonlijke access token:

```bash
git push -u origin <jouw-branch>
```

Bij het opgeven van credentials gebruik je je GitHub-gebruikersnaam en de PAT
als wachtwoord. Als `main` beschermd is, kies dan een nieuwe branch
bijvoorbeeld `codex/services-blueprint` en maak daarna een pull request.

## 6. Laat Codex de samengevoegde branch ophalen

Wanneer de commit(s) op GitHub staan, laat Codex simpelweg:

```bash
git fetch origin
git checkout <jouw-branch>
git pull
```

## 7. Controleer dat de services-structuur klopt

Valideer tot slot dat de services in jouw lokale clone overeenkomen met het
bouwplan:

```bash
ls services/trading_core/trading_core
ls services/trader_signal_engine/app
ls services/trader_executor/app
ls services/trader_pnl_orchestrator/app
ls services/trader_universe_selector/app
```

Je zou in `services/trading_core/trading_core` minimaal `__init__.py`,
`decision.py`, `executor.py` en `metrics.py` moeten zien. De overige services
moeten elk een `app`-pakket bevatten met `__init__.py`, `main.py`, `metrics.py`
en de exports-map volgens het blueprint. Wanneer dit er allemaal staat, weet je
zeker dat jouw repository gelijkloopt met de Codex-versie en kun je verder
ontwikkelen op basis van dezelfde structuur.

doen om dezelfde basis te krijgen. Vanaf dat moment werken jullie op een
consistente codebase.

Met deze workflow staan zowel de Codex-wijzigingen als jouw lokale wijzigingen
op GitHub, waarna verdere iteratie mogelijk is.
