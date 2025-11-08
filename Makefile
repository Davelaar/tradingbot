SHELL := /bin/bash
.PHONY: help logs-core logs-submit redis-scan inject-dry auth-check

help:
	@echo "Targets:"
	@echo "  make logs-core      - tail core logs"
	@echo "  make logs-submit    - tail submitter logs"
	@echo "  make redis-scan     - toon laatste van orders:live & orders:executed"
	@echo "  make inject-dry     - injecteer 1 DRY test-order in orders:live"
	@echo "  make auth-check     - Bitvavo auth (geen orders)"

logs-core:
	@systemctl --no-pager --plain status trading-core.service | sed -n '1,10p'
	@echo "--- tail (Ctrl+C om te stoppen) ---"
	@journalctl -fu trading-core.service

logs-submit:
	@systemctl --no-pager --plain status trading-submit.service | sed -n '1,10p'
	@echo "--- tail (Ctrl+C om te stoppen) ---"
	@journalctl -fu trading-submit.service

redis-scan:
	@/srv/trading/.venv/bin/python - <<'PY'
from redis import Redis
r=Redis.from_url("redis://127.0.0.1:6379/0",decode_responses=True)
print("orders:live     LAST =", r.xrevrange("orders:live", count=1))
print("orders:executed LAST =", r.xrevrange("orders:executed", count=1))
PY

inject-dry:
	@/srv/trading/.venv/bin/python - <<'PY'
from redis import Redis; import time, json, os
r=Redis.from_url("redis://127.0.0.1:6379/0",decode_responses=True)
body={"market":"HONEY-EUR","side":"buy","orderType":"market","amount":"0.000000","price":None,"mode":"percent","tp_pct":"2.0000","sl_pct":"1.0000","src":"vscode_make_dry","ts":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
mid=r.xadd("orders:live", {"data": json.dumps(body)})
print("XADD orders:live ->", mid)
time.sleep(1)
print("orders:executed LAST =", r.xrevrange("orders:executed", count=1))
PY

auth-check:
	@python3 - <<'PY'
import os, re, json, sys
cfg={}
for f in ("/etc/trading/submitter.env","/srv/trading/secrets/bitvavo.env"):
    try:
        with open(f) as fh:
            for line in fh:
                m=re.match(r'(BITVAVO_[A-Z_]+)=(.*)', line.strip())
                if m: cfg[m.group(1)]=m.group(2)
    except: pass
key=cfg.get("BITVAVO_API_KEY"); sec=cfg.get("BITVAVO_API_SECRET")
if not key or not sec:
    print("AUTH: MISSING_CREDS"); sys.exit(0)
from python_bitvavo_api.bitvavo import Bitvavo
bv=Bitvavo({"APIKEY":key,"APISECRET":sec})
try:
    bal=bv.balance({"symbol":"EUR"})
    print("AUTH: OK, EUR balance entries =", len(bal))
except Exception as e:
    print("AUTH: ERROR", str(e)[:200])
PY
