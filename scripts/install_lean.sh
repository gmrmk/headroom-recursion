#!/usr/bin/env bash
# Idempotent Lean 4 + Mathlib installer for the rung-1 oracle backend.
#
# Levels of success (the doctor reports which one was reached):
#   mathlib   - lake project builds a Mathlib-importing file (full rung 1)
#   core-lean - lean compiles a self-contained file (decider limited to
#               stdlib-only lemmas; gate fully functional)
#   none      - no lean; the gate degrades to pass-with-note, never blocks
#
# Safe to re-run: every step checks before acting. Logs everything it does.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LEAN_PROJECT="$REPO_DIR/lean"
LOG="${INSTALL_LEAN_LOG:-$REPO_DIR/runs/install-lean.log}"
mkdir -p "$(dirname "$LOG")"

# The agent proxy MITMs TLS; every HTTPS client in this chain must trust its CA.
if [ -f /root/.ccr/ca-bundle.crt ]; then
    export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt
    export CURL_CA_BUNDLE=/root/.ccr/ca-bundle.crt
fi
export PATH="$HOME/.elan/bin:$PATH"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
run() { log "+ $*"; "$@" >>"$LOG" 2>&1; }

status() {  # highest level currently working
    if (cd "$LEAN_PROJECT" && lake env lean LeanOracle/Smoke.lean) >/dev/null 2>&1; then
        echo mathlib
    elif command -v lean >/dev/null 2>&1 \
        && echo 'theorem t : 1 = 1 := rfl' | lean --stdin >/dev/null 2>&1; then
        echo core-lean
    else
        echo none
    fi
}

log "=== install_lean.sh start (current level: $(status)) ==="

# 1+2. toolchain (pinned by lean/lean-toolchain) ----------------------------
# Preferred: elan + release binaries. Fallback (egress policies that block
# GitHub release downloads but allow git): build the pinned tag from source —
# same tag => same githash => the Mathlib olean cache stays compatible.
TOOLCHAIN="$(cat "$LEAN_PROJECT/lean-toolchain")"
TAG="${TOOLCHAIN#*:}"
SRC_DIR="$HOME/lean4-src"
STAGE_BIN="$SRC_DIR/build/release/stage1/bin"

have_toolchain() {
    command -v lean >/dev/null 2>&1 && lean --version 2>/dev/null | grep -q "${TAG#v}"
}

if ! have_toolchain && [ -x "$STAGE_BIN/lean" ]; then
    export PATH="$STAGE_BIN:$PATH"
fi

if ! have_toolchain; then
    if ! command -v elan >/dev/null 2>&1; then
        log "installing elan"
        run sh -c 'curl -sSf https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --default-toolchain none' \
            || log "elan install failed (release downloads may be egress-blocked)"
        hash -r
    fi
    if command -v elan >/dev/null 2>&1; then
        log "installing toolchain $TOOLCHAIN via elan"
        run elan toolchain install "$TOOLCHAIN" || log "elan toolchain install failed"
        run elan default "$TOOLCHAIN" || true
    fi
fi

if ! have_toolchain; then
    log "falling back to FROM-SOURCE build of lean4 @ $TAG (sanctioned git route; ~1-2h)"
    if [ ! -d "$SRC_DIR/.git" ]; then
        run git clone --depth 1 --branch "$TAG" https://github.com/leanprover/lean4 "$SRC_DIR" \
            || { log "FATAL: git clone of lean4 failed. Level: $(status)"; exit 1; }
    fi
    cd "$SRC_DIR"
    run cmake --preset release || { log "FATAL: cmake configure failed. Level: $(status)"; exit 1; }
    if ! run make -C build/release -j"$(nproc)" stage1; then
        log "FATAL: lean4 source build failed. Level: $(status)"
        exit 1
    fi
    export PATH="$STAGE_BIN:$PATH"
    # Make the toolchain visible to later shells (doctor, campaign).
    for tool in lean lake; do
        ln -sf "$STAGE_BIN/$tool" /usr/local/bin/$tool 2>/dev/null || true
    done
fi
log "toolchain: $(lean --version 2>/dev/null || echo missing) | lake: $(command -v lake || echo missing)"

# 3. mathlib + olean cache --------------------------------------------------
cd "$LEAN_PROJECT"
if [ ! -d .lake/packages/mathlib ]; then
    log "lake update (cloning mathlib @ pinned rev)"
    run lake update || { log "FATAL: lake update failed. Level: $(status)"; exit 1; }
fi
log "fetching prebuilt olean cache (large download)"
if ! run lake exe cache get; then
    log "WARNING: cache fetch failed - NOT building Mathlib from source (hours)."
    log "Core Lean remains usable. Level: $(status)"
    exit 2
fi

# 4. smoke ------------------------------------------------------------------
log "smoke: compiling LeanOracle/Smoke.lean (first Mathlib import is slow)"
if run lake env lean LeanOracle/Smoke.lean; then
    log "=== SUCCESS: level mathlib ==="
    exit 0
fi
log "smoke failed after cache fetch; trying 'lake build' of the lib once"
if run lake build; then
    log "=== SUCCESS: level mathlib (after lake build) ==="
    exit 0
fi
log "WARNING: Mathlib smoke failed. Level: $(status)"
exit 2
