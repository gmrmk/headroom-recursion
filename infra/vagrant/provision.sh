#!/usr/bin/env bash
# OSINT Goblin VM provisioning -- runs as the vagrant user on first boot.
# Idempotent: re-running is safe.

set -euo pipefail

echo "[osint-goblin-vm] === provision start ==="

# ---------------------------------------------------------------------------
# 1. tmpfs /tmp (RAM-backed) -- browser tempdirs never touch disk
# ---------------------------------------------------------------------------
# Browser caches, Playwright tempdirs, Chromium GPU shader cache, etc. all
# land under /tmp by default. Mounting /tmp as tmpfs means they live in RAM,
# never on disk, and evaporate at every reboot. This is core to the
# "files never touch hard drive" doctrine.
if ! grep -q "tmpfs /tmp" /etc/fstab; then
  echo "[osint-goblin-vm] adding tmpfs /tmp to /etc/fstab"
  echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,size=2G 0 0" | sudo tee -a /etc/fstab
  # Don't remount mid-provisioning -- changes apply on reboot. Vagrant
  # will reboot at the end of provisioning if config says so.
fi

# ---------------------------------------------------------------------------
# 2. APT packages: base toolchain
# ---------------------------------------------------------------------------
sudo apt-get update -qq
sudo apt-get install -y \
  build-essential \
  git \
  curl \
  ca-certificates \
  pkg-config \
  libssl-dev \
  python3 \
  python3-pip \
  python3-venv \
  wireguard \
  resolvconf \
  cmake

# ---------------------------------------------------------------------------
# 3. uv (Python package manager) -- canonical install
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "[osint-goblin-vm] installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi
export PATH="$HOME/.local/bin:$PATH"

# ---------------------------------------------------------------------------
# 4. Node.js via fnm (frozen node version manager) + pnpm
# ---------------------------------------------------------------------------
if ! command -v fnm >/dev/null 2>&1; then
  echo "[osint-goblin-vm] installing fnm"
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell
  echo 'export PATH="$HOME/.local/share/fnm:$PATH"' >> "$HOME/.bashrc"
  echo 'eval "$(fnm env --use-on-cd)"' >> "$HOME/.bashrc"
fi
export PATH="$HOME/.local/share/fnm:$PATH"
eval "$(fnm env --use-on-cd 2>/dev/null || true)"
fnm install --lts || true
fnm use lts-latest || true

if ! command -v pnpm >/dev/null 2>&1; then
  npm install -g pnpm
fi

# ---------------------------------------------------------------------------
# 5. just (cross-platform task runner -- repo uses justfile)
# ---------------------------------------------------------------------------
if ! command -v just >/dev/null 2>&1; then
  echo "[osint-goblin-vm] installing just"
  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | \
    bash -s -- --to "$HOME/.local/bin"
fi

# ---------------------------------------------------------------------------
# 6. Playwright system dependencies (Chromium needs libnss3 etc.)
# ---------------------------------------------------------------------------
sudo apt-get install -y \
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
  || echo "[osint-goblin-vm] WARN: some Playwright deps may need manual install"

# ---------------------------------------------------------------------------
# 7. Helpful aliases + banner
# ---------------------------------------------------------------------------
cat >> "$HOME/.bashrc" <<'BASHRC_EOF'

# osint-goblin-vm convenience
alias ll='ls -la'
alias goblin='cd /vagrant'
alias smoke='cd /vagrant && just smoke'

# Banner
if [ -t 1 ] && [ "${VAGRANT_PROVISION_BANNER_SHOWN:-}" != "1" ]; then
  export VAGRANT_PROVISION_BANNER_SHOWN=1
  echo ""
  echo "OSINT Goblin VM -- you are inside the isolated investigation VM."
  echo "  goblin      cd to /vagrant (repo mount)"
  echo "  just smoke  health-check the dev stack"
  echo "  exit        leave the VM (or 'vagrant halt' from host to stop it)"
  echo ""
fi
BASHRC_EOF

echo "[osint-goblin-vm] === provision complete ==="
echo "[osint-goblin-vm] tmpfs /tmp will mount on next reboot."
echo "[osint-goblin-vm] Next: \`vagrant ssh\` then \`cd /vagrant && just install\`."
