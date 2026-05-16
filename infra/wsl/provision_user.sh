#!/usr/bin/env bash
# WSL2 Ubuntu -- user-space provisioning.
# Invoked via: wsl -d Ubuntu -- bash /mnt/c/.../provision_user.sh
# Runs as the default WSL user. All installs land under $HOME/.local/.
# Idempotent: re-runs are safe.

set -euo pipefail

LINUX_REPO="$HOME/workingtitle"
WINDOWS_REPO="/mnt/c/Users/strid/osint-goblin"
GITHUB_REMOTE="https://github.com/gmrmk/workingtitle.git"

log() { echo "[osint-wsl-user] $*"; }

# Ensure $HOME/.local/bin and fnm dir are on PATH for the rest of this script.
mkdir -p "$HOME/.local/bin" "$HOME/.local/share"
export PATH="$HOME/.local/bin:$HOME/.local/share/fnm:$PATH"

# ---------------------------------------------------------------------------
# 1. uv (Python package manager)
# ---------------------------------------------------------------------------
log "step 1/4: uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  log "  uv installed"
else
  log "  skip (uv already present)"
fi
hash -r 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. fnm + Node LTS + pnpm
# ---------------------------------------------------------------------------
log "step 2/4: Node LTS via fnm"
if ! command -v fnm >/dev/null 2>&1; then
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell >/dev/null
  log "  fnm installed"
else
  log "  fnm already present"
fi
hash -r 2>/dev/null || true
eval "$(fnm env --use-on-cd 2>/dev/null || true)"
if ! fnm list 2>/dev/null | grep -q lts-latest; then
  fnm install --lts >/dev/null
fi
fnm use lts-latest >/dev/null 2>&1 || true
if ! command -v pnpm >/dev/null 2>&1; then
  npm install -g pnpm >/dev/null 2>&1
  log "  pnpm installed"
else
  log "  pnpm already present"
fi

# ---------------------------------------------------------------------------
# 3. just (task runner)
# ---------------------------------------------------------------------------
log "step 3/4: just"
if ! command -v just >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | \
    bash -s -- --to "$HOME/.local/bin" >/dev/null
  log "  just installed"
else
  log "  skip (just already present)"
fi

# ---------------------------------------------------------------------------
# 4. Linux-native working copy
# ---------------------------------------------------------------------------
log "step 4/4: Linux-native working copy at $LINUX_REPO"
if [ ! -d "$LINUX_REPO/.git" ]; then
  if [ -d "$WINDOWS_REPO/.git" ]; then
    log "  cloning from local $WINDOWS_REPO"
    git clone --no-hardlinks "$WINDOWS_REPO" "$LINUX_REPO" >/dev/null
    cd "$LINUX_REPO"
    git remote set-url origin "$GITHUB_REMOTE"
  else
    log "  cloning from GitHub: $GITHUB_REMOTE"
    git clone "$GITHUB_REMOTE" "$LINUX_REPO"
  fi
else
  log "  skip (working copy already exists at $LINUX_REPO)"
fi

# Convenience PATH + aliases in .bashrc (idempotent).
if ! grep -q '# osint-goblin convenience' "$HOME/.bashrc" 2>/dev/null; then
  cat >> "$HOME/.bashrc" <<BASHRC

# osint-goblin convenience
export PATH="\$HOME/.local/bin:\$HOME/.local/share/fnm:\$PATH"
eval "\$(fnm env --use-on-cd 2>/dev/null || true)"
alias goblin='cd $LINUX_REPO'
BASHRC
  log "  bashrc aliases added (new shells will pick them up)"
fi

log "user provisioning done"
echo ""
echo "============================================================"
echo " OSINT Goblin is ready inside WSL2 Ubuntu."
echo "   Linux working copy: $LINUX_REPO"
echo "   Next:  goblin && just install && just test"
echo "============================================================"
