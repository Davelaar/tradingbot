#!/usr/bin/env python3
import os, time, decimal as D
from typing import Dict
from redis import Redis
from binance.client import Client   # pip install python-binance

R = Redis.from_url(os.getenv("REDIS_URL","redis://127.0.0.1:6379/0"), decode_responses=True)
API_KEY    = os.getenv("BINANCE_API_KEY","")
API_SECRET = os.getenv("BINANCE_API_SECRET","")

# Welke quote tellen we als EUR-equivalent?
QUOTE = os.getenv("QUOTE_SYMBOL","EUR")  # bijv. “EUR”
# Fallback tickers (prijs in EUR) ophalen via Binance
PRICE_CACHE_TTL = 30

def get_prices_eur(client: Client) -> Dict[str, D.Decimal]:
    """Haal prijzen op voor alle assets t.o.v. EUR (bv. BTC/EUR, ETH/EUR, ...).
       Waar geen direct paar bestaat, laten we die asset op 0 (niet meetellen)."""
    prices: Dict[str, D.Decimal] = {}
    try:
        tickers = client.get_symbol_ticker()
        # Map naar {symbol: price}
        raw = {t["symbol"]: D.Decimal(t["price"]) for t in tickers}
        # Vul EUR-paren
        for sym, px in raw.items():
            if sym.endswith(QUOTE):
                base = sym[:-len(QUOTE)]
                prices[base] = px
    except Exception:
        pass
    return prices

def main():
    if not API_KEY or not API_SECRET:
        print("[balance-sync] BINANCE_API_KEY/SECRET ontbreekt; stop.")
        return
    client = Client(api_key=API_KEY, api_secret=API_SECRET)

    while True:
        try:
            acct = client.get_account()
            balances = {b["asset"]: (D.Decimal(b["free"]), D.Decimal(b["locked"])) for b in acct["balances"]}
            prices = get_prices_eur(client)

            free_eur  = D.Decimal("0")
            total_eur = D.Decimal("0")

            # Schrijf balances in Redis (optioneel)
            pipe = R.pipeline()
            pipe.delete("balances:free", "balances:locked")

            for asset, (free, locked) in balances.items():
                if asset == QUOTE:
                    free_eur  += free
                    total_eur += (free + locked)
                else:
                    px = prices.get(asset, D.Decimal("0"))
                    if px > 0:
                        total_eur += (free + locked) * px
                # bewaar rauwe aantallen
                if (free > 0) or (locked > 0):
                    pipe.hset("balances:free",  asset, str(free))
                    pipe.hset("balances:locked",asset, str(locked))

            # Zet de sleutels die core gebruikt:
            pipe.set("account:free_eur",  str(free_eur))
            pipe.set("account:eur_balance", str(free_eur))  # backward-compat
            pipe.set("account:total_equity_eur", str(total_eur))
            pipe.execute()

            print(f"[balance-sync] free_eur={free_eur} total_eur={total_eur}")
        except Exception as e:
            print(f"[balance-sync] error: {e}")

        time.sleep(int(os.getenv("BAL_SYNC_INTERVAL","15")))

if __name__ == "__main__":
    main()
