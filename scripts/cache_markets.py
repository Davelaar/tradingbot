#!/usr/bin/env python3
"""
cache_markets.py — schrijft precisies/minima per market weg voor snelle lookups

Output:
  /srv/trading/storage/markets_precision.json  (atomisch weggeschreven)

Structuur:
{
  "generatedAt": 1762797000,
  "markets": {
    "TREE-EUR": {"pp":2,"ap":2,"minBase":0.0,"minQuote":5.0},
    "ICP-EUR":  {"pp":2,"ap":6,"minBase":0.01,"minQuote":5.0},
    ...
  }
}
"""
import os, json, time, urllib.request, urllib.error, tempfile

OUT = "/srv/trading/storage/markets_precision.json"
URL = "https://api.bitvavo.com/v2/markets"

def fetch_markets():
    req = urllib.request.Request(URL, headers={"User-Agent":"cache-markets/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def build_index(rows):
    idx = {}
    for m in rows:
        mk = str(m.get("market","")).upper()
        if not mk:
            continue
        prec = m.get("precision") or {}
        pp = m.get("pricePrecision", prec.get("price", None))
        ap = m.get("amountPrecision", prec.get("amount", None))
        # sommige velden heten minOrderInQuote/minOrderInBase
        min_q = m.get("minOrderInQuote", 0) or 0
        min_b = m.get("minOrderInBase", 0) or 0
        # normaliseer types
        try: pp = int(pp)
        except: pp = 2
        try: ap = int(ap)
        except: ap = 6
        try: min_q = float(min_q)
        except: min_q = 0.0
        try: min_b = float(min_b)
        except: min_b = 0.0
        idx[mk] = {"pp": pp, "ap": ap, "minBase": min_b, "minQuote": min_q}
    return {
        "generatedAt": int(time.time()),
        "markets": idx
    }

def atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".markets_", dir=os.path.dirname(path))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, separators=(",",":"))
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

if __name__ == "__main__":
    try:
        rows = fetch_markets()
        if not isinstance(rows, list):
            raise RuntimeError("unexpected response")
        data = build_index(rows)
        atomic_write(OUT, data)
        print("[ok] wrote", OUT, "markets:", len(data["markets"]))
    except Exception as e:
        print("[error]", str(e))
        raise