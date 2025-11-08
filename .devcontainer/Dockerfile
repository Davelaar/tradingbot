FROM mcr.microsoft.com/devcontainers/python:3.12

# System deps die handig zijn in dit project
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git curl jq redis-tools procps nano ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Werkmap
WORKDIR /workspaces/tradingbot
