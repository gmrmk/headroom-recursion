#!/usr/bin/env bash
# WSL2 Ubuntu -- system-level provisioning (root context).
# Invoked via: wsl -d Ubuntu -u root -- bash /mnt/c/.../provision_root.sh
# Skips sudo entirely because we already run as root in this invocation.
# Idempotent: re-runs are safe.

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

log() { echo "[osint-wsl-root] $*"; }

log "step 1/2: apt base packages"
apt-get update -qq
apt-get install -y -qq \
  build-essential \
  curl \
  ca-certificates \
  git \
  pkg-config \
  libssl-dev \
  python3 \
  python3-pip \
  python3-venv \
  wireguard-tools \
  resolvconf \
  cmake \
  unzip

log "step 2/2: Playwright/Chromium system libs"
apt-get install -y -qq \
  libnss3 \
  libnspr4 \
  libatk1.0-0 \
  libatk-bridge2.0-0 \
  libcups2 \
  libxkbcommon0 \
  libatspi2.0-0 \
  libxcomposite1 \
  libxdamage1 \
  libxfixes3 \
  libxrandr2 \
  libgbm1 \
  libpango-1.0-0 \
  libcairo2 \
  libasound2 \
  || log "  WARN: some Playwright deps not available; continuing"

log "root provisioning done"
