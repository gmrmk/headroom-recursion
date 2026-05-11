#!/usr/bin/env bash
# start-dev.sh — POSIX parity launcher for OSINT Dashboard (macOS / Linux / WSL2).
#
# Mirrors start-dev.ps1's idempotency contract: re-runs never recreate the venv,
# never double-bind ports, resume from any partial failure.
#
# Exit codes match the .ps1 (2 prereq / 3 redis / 4 venv / 5 pnpm / 6 health).

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
WEB_DIR="$REPO_ROOT/apps/web"
API_PORT=8000
WEB_PORT=3000
REDIS_PORT=6379
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
SKIP_HEALTH="${SKIP_HEALTH:-0}"
NO_BROWSER="${NO_BROWSER:-0}"

# ----- formatters ----------------------------------------------------------
if [ -t 1 ]; then
    C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_CYAN=""; C_GREEN=""; C_YEL=""; C_RED=""; C_OFF=""
fi
step() { printf "%s==> %s%s\n" "$C_CYAN" "$*" "$C_OFF"; }
ok()   { printf "    %sOK%s   %s\n" "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf "    %sWARN%s %s\n" "$C_YEL"   "$C_OFF" "$*"; }
err()  { printf "    %sERR%s  %s\n" "$C_RED"   "$C_OFF" "$*"; }

port_in_use() {
    # Returns 0 iff something is listening on $1.
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$1" -sTCP:LISTEN -P -n >/dev/null 2>&1
    else
        # Fallback: probe with bash /dev/tcp
        (exec 3<>/dev/tcp/127.0.0.1/"$1") >/dev/null 2>&1
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# ----- 1. prerequisites ----------------------------------------------------
step "Checking prerequisites"

if ! have python3 && ! have python; then
    err "python3 not on PATH"; exit 2
fi
PYTHON="$(command -v python3 || command -v python)"
PY_VER="$("$PYTHON" --version 2>&1)"
case "$PY_VER" in
    *"3.13"*|*"3.14"*|*"3.15"*) ok "$PY_VER" ;;
    *) warn "$PY_VER detected, project targets 3.13+" ;;
esac

if ! have node; then err "node not on PATH"; exit 2; fi
ok "Node $(node --version)"

if ! have pnpm; then
    step "Provisioning pnpm via corepack"
    if have corepack; then
        corepack enable >/dev/null 2>&1 || true
        corepack prepare pnpm@latest --activate >/dev/null 2>&1 || true
    fi
    if ! have pnpm; then err "pnpm provisioning failed"; exit 2; fi
fi
ok "pnpm $(pnpm --version)"

# ----- 2 & 3. venv + deps --------------------------------------------------
if [ ! -x "$VENV_DIR/bin/python" ]; then
    step "Creating venv at $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR" || { err "venv creation failed"; exit 4; }
else
    ok "venv exists, reusing"
fi

VENV_PY="$VENV_DIR/bin/python"

if have uv; then
    step "uv sync"
    uv sync --python "$VENV_PY" || { err "uv sync failed"; exit 4; }
else
    step "pip install -e .[dev] (uv not found, 'pip install uv' for 10x speed)"
    "$VENV_PY" -m pip install --upgrade pip >/dev/null
    "$VENV_PY" -m pip install -e ".[dev]" || { err "pip install failed"; exit 4; }
fi
ok "Python deps synced"

# ----- 4. pnpm install -----------------------------------------------------
if [ ! -d "$WEB_DIR" ]; then
    warn "apps/web missing — skipping frontend bootstrap"
else
    step "pnpm install (apps/web)"
    if ! (cd "$WEB_DIR" && pnpm install --frozen-lockfile); then
        warn "frozen-lockfile failed, retrying without it"
        (cd "$WEB_DIR" && pnpm install) || { err "pnpm install failed"; exit 5; }
    fi
    ok "pnpm deps synced"
fi

# ----- 5. redis ------------------------------------------------------------
if port_in_use "$REDIS_PORT"; then
    ok "Redis listening on :$REDIS_PORT"
else
    if have redis-server; then
        step "Starting redis-server in background"
        redis-server --daemonize yes --port "$REDIS_PORT"
        sleep 0.5
        port_in_use "$REDIS_PORT" && ok "redis-server up" || warn "redis-server did not bind"
    elif have docker; then
        step "Starting redis via docker"
        docker run -d --rm --name osint-redis -p "$REDIS_PORT:6379" redis:7-alpine >/dev/null
        sleep 0.7
        port_in_use "$REDIS_PORT" && ok "docker redis up" || warn "docker redis did not bind"
    else
        err "No redis-server or docker available"
        exit 3
    fi
fi

# ----- 6. spawn dev panes --------------------------------------------------
CMD_API="\"$VENV_PY\" -m uvicorn osint_api.main:app --reload --port $API_PORT --reload-dir src"
CMD_WORKER="\"$VENV_PY\" -m dramatiq osint_api.workers --watch src --processes 1 --threads 4"
CMD_WEB="pnpm --dir \"$WEB_DIR\" dev --port $WEB_PORT"

spawn_in_background() {
    local label="$1" cmd="$2"
    mkdir -p "$REPO_ROOT/.dev-logs"
    local log="$REPO_ROOT/.dev-logs/$label.log"
    bash -c "$cmd" > "$log" 2>&1 &
    echo $! > "$REPO_ROOT/.dev-logs/$label.pid"
    ok "$label started (pid $(cat "$REPO_ROOT/.dev-logs/$label.pid"), logs: $log)"
}

if port_in_use "$API_PORT"; then ok "API already running on :$API_PORT"
else spawn_in_background "osint-api" "$CMD_API"; fi

# Worker doesn't bind a port, so check pidfile freshness
if [ -f "$REPO_ROOT/.dev-logs/osint-worker.pid" ] \
    && kill -0 "$(cat "$REPO_ROOT/.dev-logs/osint-worker.pid")" 2>/dev/null; then
    ok "Worker already running (pid $(cat "$REPO_ROOT/.dev-logs/osint-worker.pid"))"
else
    spawn_in_background "osint-worker" "$CMD_WORKER"
fi

if port_in_use "$WEB_PORT"; then ok "Web already running on :$WEB_PORT"
else spawn_in_background "osint-web" "$CMD_WEB"; fi

# ----- 7. health -----------------------------------------------------------
if [ "$SKIP_HEALTH" = "1" ]; then exit 0; fi

step "Waiting up to ${HEALTH_TIMEOUT}s for /healthz and / to return 200"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
api_ok=0; web_ok=0
while [ "$(date +%s)" -lt "$deadline" ] && [ "$api_ok$web_ok" != "11" ]; do
    if [ "$api_ok" = 0 ] && curl -fsS "http://localhost:$API_PORT/healthz" >/dev/null 2>&1; then
        api_ok=1; ok "FastAPI green"
    fi
    if [ "$web_ok" = 0 ] && curl -fsS -o /dev/null -w "%{http_code}" "http://localhost:$WEB_PORT/" 2>/dev/null \
            | grep -qE '^(2|3|4)'; then
        web_ok=1; ok "Next.js green"
    fi
    sleep 0.7
done

if [ "$api_ok$web_ok" != "11" ]; then
    err "Health timeout (api=$api_ok web=$web_ok). Tail .dev-logs/*.log for stack traces."
    exit 6
fi

if [ "$NO_BROWSER" != 1 ]; then
    if   have open;     then open "http://localhost:$WEB_PORT/"
    elif have xdg-open; then xdg-open "http://localhost:$WEB_PORT/" >/dev/null 2>&1 &
    fi
fi
printf "\n%sDev stack ready — tail .dev-logs/*.log or kill via scripts/stop-dev.sh%s\n" "$C_GREEN" "$C_OFF"
