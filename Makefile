SHELL := /bin/bash
.PHONY: help logs-core logs-submit redis-scan

help:
	@echo "Targets:"
	@echo "  make logs-core      - tail core logs"
	@echo "  make logs-submit    - tail submitter logs"
	@echo "  make redis-scan     - toon laatste van orders:live & orders:executed"

logs-core:
	@systemctl --no-pager --plain status trading-core.service | sed -n '1,10p'
	@echo "--- tail (Ctrl+C om te stoppen) ---"
	@journalctl -fu trading-core.service

logs-submit:
	@systemctl --no-pager --plain status trading-submit.service | sed -n '1,10p'
	@echo "--- tail (Ctrl+C om te stoppen) ---"
	@journalctl -fu trading-submit.service

redis-scan:
	@/srv/trading/.venv/bin/python -c 'from redis import Redis;r=Redis.from_url("redis://127.0.0.1:6379/0",decode_responses=True);print("orders:live     LAST =", r.xrevrange("orders:live", count=1));print("orders:executed LAST =", r.xrevrange("orders:executed", count=1))'
