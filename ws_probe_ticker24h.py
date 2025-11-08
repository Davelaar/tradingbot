import os, time, json
from python_bitvavo_api.bitvavo import Bitvavo

m = os.getenv("PROBE_MARKET", "BTC-EUR")
bv = Bitvavo({'APIKEY': os.getenv("BITVAVO_API_KEY",""), 'APISECRET': os.getenv("BITVAVO_API_SECRET","")})
ws = bv.newWebsocket()

def on_err(e): print("[err]", e)
def on_tick(payload):
    if isinstance(payload, dict):
        print("[ticker24h] type=dict keys=", sorted(payload.keys()))
    elif isinstance(payload, list):
        print("[ticker24h] type=list len=", len(payload))
        if payload and isinstance(payload[0], dict):
            print("[ticker24h] first keys=", sorted(payload[0].keys()))
    else:
        print("[ticker24h] type=", type(payload).__name__)
    # we hebben genoeg gezien:
    ws.closeSocket()

ws.setErrorCallback(on_err)
# per SDK: per-markt subscription
ws.subscriptionTicker24h(m, on_tick)

# draai kort; socket sluit zichzelf in on_tick
for _ in range(40):
    time.sleep(0.25)
