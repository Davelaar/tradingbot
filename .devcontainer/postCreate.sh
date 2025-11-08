#!/usr/bin/env bash
set -euo pipefail
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  git jq redis-tools curl
python -m pip install --upgrade pip
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi
echo "[devcontainer] Setup complete."
