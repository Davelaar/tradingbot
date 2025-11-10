"""Legacy entrypoint kept for backwards compatibility.

The trading core service now lives under ``services.trading_core`` in
accordance met het bouwplan.  Importing
``services.trading_core.main`` houdt het bestaande CLI-gedrag intact
terwijl de nieuwe pakketstructuur zichtbaar blijft.
"""
from services.trading_core.main import main


if __name__ == "__main__":
    main()
