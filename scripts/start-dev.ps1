# start-dev.ps1 -- Win11 native one-command dev launcher for OSINT GOBLIN.
#
# What it does (idempotent):
#   1. Verifies prerequisites (Python 3.13, Node 20+, pnpm, Memurai or Redis).
#   2. With -Init: creates the minimum scaffolding the script needs to
#      survive Day-1 fresh-clone (apps/, packages/, src/ markers, .dev-logs/,
#      data/) so that subsequent steps don't trip on missing directories.
#      Without -Init: assumes the scaffold exists (manufactured project state).
#   3. Creates / re-uses .venv at repo root via `python -m venv`.
#   4. Syncs Python deps via `uv sync` (falls back to `pip install -e ".[dev]"`).
#   5. Ensures pnpm install is up to date for apps/web.
#   6. Mode-aware storage substrate:
#        - m0 (default, Win11 brief sec6): SQLite + Memurai + local-fs MinIO,
#          no Docker required for inner loop. Aligns with Boris D2 sec11.1.
#        - m1: docker compose up (Postgres+AGE + MinIO + Memurai sibling);
#          opt-in once Docker Desktop is installed.
#   7. Boots three foreground panes in one Windows Terminal window:
#        - FastAPI       (uvicorn --reload --reload-dir src, port 8000)
#        - Dramatiq      (--watch src --processes 1 --threads 4)
#        - Next.js dev   (pnpm dev, port 3000, Turbopack HMR)
#   8. Tails health checks until /healthz and / both return 200.
#
# Idempotency contract:
#   - Re-running NEVER recreates the venv. It only adds missing packages.
#   - Re-running NEVER spawns a second uvicorn / dramatiq / next-dev if one is
#     already bound to its expected port. The wt invocation is skipped instead.
#   - Re-running is safe under partial-failure: if pnpm install crashed last
#     time, the next run resumes from `pnpm install` -- no half-state.
#   - -Init is also idempotent: it only creates directories that don't already
#     exist; never overwrites existing files.
#
# Exit codes:
#   0 -- all three services reachable
#   2 -- prerequisite missing (Python / Node / pnpm)
#   3 -- Memurai not installed and no fallback Redis found
#   4 -- venv creation or `uv sync` failed
#   5 -- pnpm install failed
#   6 -- service did not become healthy within HealthTimeoutSeconds
#   7 -- Init requested but a repo-marker is missing AND the script can't
#       guess where to scaffold (e.g. no pyproject.toml AND no .git AND no
#       INIT_REPO_ROOT env override)
#   8 -- Mode=m1 requested but Docker Desktop not reachable
#
# Cross-team contract:
#   - The committed app namespace is `osint_goblin_*` per Sora sec3.1
#     (MANUFACTURING-PLAN sec1). This script imports `osint_goblin_api.main:app`
#     and runs the worker as `osint_goblin_workers`.
#   - Mode contract aligns with Boris D2 / phase4/02-devops.md sec11.1-sec11.2.
#   - --reload-dir src and --processes 1 --threads 4 are Priya's locked
#     decisions per phase4/05-devx.md sec7.1-sec7.2. Both are Win11 SIGINT-load-bearing.

[CmdletBinding()]
param(
    [ValidateSet('m0','m1')]
    [string]$Mode = 'm0',

    # Run the one-time Day-1 scaffold step before the rest of the script.
    # On a manufactured repo this is a no-op; on a fresh clone it creates
    # the directories the FastAPI / Dramatiq / pnpm hands-off all expect.
    [switch]$Init,

    # Skip the full prereq-resolution path (used by automation / tests).
    [switch]$SkipPrereqs,

    [switch]$NoBrowser,
    [switch]$SkipHealth,
    [int]$HealthTimeoutSeconds = 60,

    # m1 only -- passed through to docker compose --profile
    [ValidateSet('age','memgraph')]
    [string]$GraphTier = 'age',

    # -Diagnose runs every prereq check non-fatally, prints a what/why/fix
    # table covering ALL failures (not just the first), then exits 0/9.
    # Spawns no services. Priya phase6 R-10 / P1 -- the "doctor" command.
    [switch]$Diagnose
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvDir    = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts/python.exe"
$WebDir     = Join-Path $RepoRoot "apps/web"
$ApiPort    = 8000
$WebPort    = 3000
$RedisPort  = 6379

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    WARN $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "    ERR  $msg" -ForegroundColor Red }

# -----------------------------------------------------------------------------
# Diagnose accumulator (Priya R-10 phase6).
#
# Three-line "what / why / fix" tables are the doctrine: a contributor whose
# stack failed to come up should see one screen of actionable text, not a
# 200-line PowerShell stack trace. Each finding goes into $script:DiagFindings
# and is printed at the end so we surface ALL problems on one pass, not just
# the first.
# -----------------------------------------------------------------------------
$script:DiagFindings = New-Object System.Collections.ArrayList

function Add-DiagFinding {
    param(
        [Parameter(Mandatory)][ValidateSet('PASS','FAIL','WARN')][string]$Status,
        [Parameter(Mandatory)][string]$What,
        [string]$Why = '',
        [string]$Fix = ''
    )
    [void]$script:DiagFindings.Add([pscustomobject]@{
        Status = $Status
        What   = $What
        Why    = $Why
        Fix    = $Fix
    })
}

function Write-Diagnose {
    # 3-line what / why / fix block. Used standalone (outside -Diagnose) when
    # a hard failure happens and we want the operator to see remediation
    # without grepping the script. Also used at the end of -Diagnose to render
    # the accumulated table.
    param(
        [Parameter(Mandatory)][string]$What,
        [Parameter(Mandatory)][string]$Why,
        [Parameter(Mandatory)][string]$Fix
    )
    Write-Host ""
    Write-Host "  WHAT: $What" -ForegroundColor Red
    Write-Host "  WHY : $Why"  -ForegroundColor Yellow
    Write-Host "  FIX : $Fix"  -ForegroundColor Cyan
    Write-Host ""
}

function Invoke-Diagnose {
    # Run every prereq check non-fatally, collect findings, print one table,
    # exit 0 if all PASS/WARN or 9 if any FAIL. Never spawns a service.
    Write-Step "Diagnose mode -- running all prereq checks (no services will spawn)"

    # 1. Python
    $python = Resolve-Tool "python"
    if (-not $python) {
        Add-DiagFinding -Status FAIL -What "Python on PATH" `
            -Why "python.exe not resolvable via Get-Command" `
            -Fix "Install Python 3.13 from python.org and tick 'Add to PATH'"
    } else {
        $pyVersion = & $python --version 2>&1
        if ($pyVersion -notmatch "3\.1[3-9]") {
            Add-DiagFinding -Status WARN -What "Python version" `
                -Why "$pyVersion detected, project targets 3.13+" `
                -Fix "Install 3.13: winget install Python.Python.3.13"
        } else {
            Add-DiagFinding -Status PASS -What "Python $pyVersion"
        }
    }

    # 2. Node
    $node = Resolve-Tool "node"
    if (-not $node) {
        Add-DiagFinding -Status FAIL -What "Node on PATH" `
            -Why "node not resolvable via Get-Command" `
            -Fix "Install Node 20+: winget install OpenJS.NodeJS.LTS"
    } else {
        $nodeVersion = & $node --version
        if ($nodeVersion -notmatch "v(2[0-9]|[3-9][0-9])\.") {
            Add-DiagFinding -Status WARN -What "Node version" `
                -Why "$nodeVersion is below 20.x, Next.js 15 expects 20+" `
                -Fix "Upgrade: winget upgrade OpenJS.NodeJS.LTS"
        } else {
            Add-DiagFinding -Status PASS -What "Node $nodeVersion"
        }
    }

    # 3. pnpm
    $pnpm = Resolve-Tool "pnpm"
    if (-not $pnpm) {
        Add-DiagFinding -Status WARN -What "pnpm on PATH" `
            -Why "pnpm not resolvable, will be auto-provisioned by corepack on next non-diagnose run" `
            -Fix "Or provision now: corepack enable; corepack prepare pnpm@latest --activate"
    } else {
        Add-DiagFinding -Status PASS -What "pnpm $(& $pnpm --version)"
    }

    # 4. uv (optional but strongly preferred)
    $uv = Resolve-Tool "uv"
    if (-not $uv) {
        Add-DiagFinding -Status WARN -What "uv on PATH" `
            -Why "uv not found; falls back to pip (10x slower sync)" `
            -Fix "pipx install uv  (or: winget install astral-sh.uv)"
    } else {
        Add-DiagFinding -Status PASS -What "uv $(& $uv --version 2>$null)"
    }

    # 5. venv
    if (-not (Test-Path $VenvPython)) {
        Add-DiagFinding -Status FAIL -What ".venv Python interpreter" `
            -Why "$VenvPython does not exist" `
            -Fix "Run: ./scripts/start-dev.ps1 -Init  (creates venv on first run)"
    } else {
        Add-DiagFinding -Status PASS -What ".venv at $VenvDir"
    }

    # 6. Redis / Memurai port
    if (Test-Port $RedisPort) {
        Add-DiagFinding -Status PASS -What "Redis-protocol service on :$RedisPort"
    } else {
        $memurai = Resolve-Tool "memurai"
        $svc = Get-Service -Name 'Memurai*' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($svc) {
            Add-DiagFinding -Status WARN -What "Memurai service installed but not running" `
                -Why "Service state: $($svc.Status)" `
                -Fix "Start-Service $($svc.Name)  (or: ./scripts/start-dev.ps1 will start it)"
        } elseif ($memurai) {
            Add-DiagFinding -Status WARN -What "memurai.exe present but no service" `
                -Why "Memurai binary on PATH but no Windows service registered" `
                -Fix "Reinstall as service: winget install Memurai.MemuraiDeveloper"
        } else {
            Add-DiagFinding -Status FAIL -What "Redis-protocol service" `
                -Why "No service on :$RedisPort and no Memurai installed" `
                -Fix "winget install Memurai.MemuraiDeveloper  (or run WSL2 redis-server)"
        }
    }

    # 7. Mode-specific (m1 only): Docker
    if ($Mode -eq 'm1') {
        try {
            & docker info 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Add-DiagFinding -Status PASS -What "Docker Desktop reachable (m1 mode)"
            } else {
                Add-DiagFinding -Status FAIL -What "Docker Desktop (m1 mode)" `
                    -Why "docker info exited $LASTEXITCODE" `
                    -Fix "Start Docker Desktop, or fall back to -Mode m0"
            }
        } catch {
            Add-DiagFinding -Status FAIL -What "Docker Desktop (m1 mode)" `
                -Why "docker command not on PATH" `
                -Fix "Install Docker Desktop, or fall back to -Mode m0"
        }
    }

    # 8. Port collisions for the services we'd spawn
    if (Test-Port $ApiPort) {
        Add-DiagFinding -Status WARN -What "API port :$ApiPort already bound" `
            -Why "Something is already listening (possibly a prior run)" `
            -Fix "Get-NetTCPConnection -LocalPort $ApiPort | Select-Object OwningProcess | Stop-Process -Force"
    } else {
        Add-DiagFinding -Status PASS -What "API port :$ApiPort free"
    }
    if (Test-Port $WebPort) {
        Add-DiagFinding -Status WARN -What "Web port :$WebPort already bound" `
            -Why "Something is already listening (possibly a prior run)" `
            -Fix "Get-NetTCPConnection -LocalPort $WebPort | Select-Object OwningProcess | Stop-Process -Force"
    } else {
        Add-DiagFinding -Status PASS -What "Web port :$WebPort free"
    }

    # 9. Repo anchors
    $anchors = @('.git','pyproject.toml','justfile','.editorconfig') |
        Where-Object { Test-Path (Join-Path $RepoRoot $_) }
    if ($anchors.Count -eq 0) {
        Add-DiagFinding -Status FAIL -What "Repo root anchor" `
            -Why "None of .git / pyproject.toml / justfile / .editorconfig at $RepoRoot" `
            -Fix "cd into the cloned osint-goblin repo before running"
    } else {
        Add-DiagFinding -Status PASS -What "Repo anchors: $($anchors -join ', ')"
    }

    # ---- Render table ----
    Write-Host ""
    Write-Host "  Diagnose report (osint-goblin dev stack)" -ForegroundColor Cyan
    Write-Host "  $('-' * 60)"
    foreach ($f in $script:DiagFindings) {
        $color = switch ($f.Status) {
            'PASS' { 'Green' }
            'WARN' { 'Yellow' }
            'FAIL' { 'Red' }
        }
        Write-Host ("  [{0,-4}] {1}" -f $f.Status, $f.What) -ForegroundColor $color
        if ($f.Why) { Write-Host ("         why: {0}" -f $f.Why) -ForegroundColor DarkGray }
        if ($f.Fix) { Write-Host ("         fix: {0}" -f $f.Fix) -ForegroundColor DarkCyan }
    }
    $fails = @($script:DiagFindings | Where-Object { $_.Status -eq 'FAIL' }).Count
    $warns = @($script:DiagFindings | Where-Object { $_.Status -eq 'WARN' }).Count
    $passes = @($script:DiagFindings | Where-Object { $_.Status -eq 'PASS' }).Count
    Write-Host ""
    Write-Host "  Summary: $passes PASS, $warns WARN, $fails FAIL" -ForegroundColor Cyan
    Write-Host ""
    if ($fails -gt 0) { exit 9 }
    exit 0
}

function Test-Port($port) {
    # Returns $true if something is already listening on $port on localhost.
    $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Resolve-Tool($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($null -ne $cmd) { return $cmd.Source }
    return $null
}

# ----------------------------------------------------------------------------
# 0. -Init -- Day-1 scaffold (idempotent; never overwrites)
# ----------------------------------------------------------------------------
function Invoke-Init {
    Write-Step "Init: ensuring Day-1 scaffold exists (idempotent)"

    # Hard repo-root anchor: presence of .git OR pyproject.toml OR justfile.
    # If none exist and we aren't given INIT_REPO_ROOT, bail early -- running
    # Init from a wrong CWD is a destructive footgun.
    $anchors = @('.git','pyproject.toml','justfile','.editorconfig') |
        Where-Object { Test-Path (Join-Path $RepoRoot $_) }
    if ($anchors.Count -eq 0 -and -not $env:INIT_REPO_ROOT) {
        Write-Err "No repo-root anchor (.git / pyproject.toml / justfile / .editorconfig) found at $RepoRoot."
        Write-Err "Set INIT_REPO_ROOT env var to override, or cd into the cloned osint-goblin repo and re-run."
        exit 7
    }

    # Scaffold directories -- only created if missing. This is the minimum the
    # rest of the script and the M0 spike require to not crash.
    $dirs = @(
        '.dev-logs',
        'data',
        'data/minio-fs',
        'apps',
        'apps/api',
        'apps/api/osint_goblin_api',
        'apps/workers',
        'apps/workers/osint_goblin_workers',
        'apps/web',
        'packages',
        'tools/ci',
        'src'   # symlink target alternative; some watch flags resolve relative paths here
    )
    foreach ($d in $dirs) {
        $path = Join-Path $RepoRoot $d
        if (-not (Test-Path $path)) {
            New-Item -ItemType Directory -Force -Path $path | Out-Null
            Write-Ok "created $d"
        }
    }

    # Minimum-viable Python module bodies so uvicorn / dramatiq don't crash on
    # `ModuleNotFoundError` during the M0 health check. Only written if absent.
    $stubs = @{
        'apps/api/osint_goblin_api/__init__.py' = "__version__ = '0.0.0'`n"
        'apps/api/osint_goblin_api/main.py' = @'
"""Day-1 placeholder FastAPI app. Replace with the real entrypoint
(packages/osint_goblin_api per Sora sec3.1) once Sprint-1 lands the real
modules. This stub exists only so start-dev.ps1's health check is meaningful
on the very first fresh-clone boot."""
from fastapi import FastAPI

app = FastAPI(title="osint-goblin (placeholder)")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "phase": "day-1-placeholder"}
'@
        'apps/workers/osint_goblin_workers/__init__.py' = "__version__ = '0.0.0'`n"
        'apps/workers/osint_goblin_workers/__main__.py' = @'
"""Day-1 placeholder Dramatiq actor module. Replace with the real
tool_runner actor (MANUFACTURING-PLAN sec1, apps/workers per Sora sec3.1)
once Sprint-1 lands evidence_pipeline."""
import dramatiq
from dramatiq.brokers.redis import RedisBroker

# This module is intentionally importable as `python -m osint_goblin_workers`
# so the dramatiq CLI can do `dramatiq osint_goblin_workers` against it.
broker = RedisBroker(url="redis://127.0.0.1:6379/0")
dramatiq.set_broker(broker)


@dramatiq.actor
def heartbeat() -> str:
    """Day-1 sentinel. Replace with the real tool_runner."""
    return "ok"
'@
    }
    foreach ($kv in $stubs.GetEnumerator()) {
        $path = Join-Path $RepoRoot $kv.Key
        if (-not (Test-Path $path)) {
            New-Item -ItemType File -Force -Path $path -Value $kv.Value | Out-Null
            Write-Ok "stubbed $($kv.Key)"
        }
    }

    Write-Ok "Init complete"
}

# -Diagnose runs ALL prereq checks non-fatally and exits. No services spawn.
# Must precede Init -- the operator may run -Diagnose specifically because
# the repo state is unhealthy and Init would itself crash.
if ($Diagnose) { Invoke-Diagnose }

if ($Init) { Invoke-Init }

# ----------------------------------------------------------------------------
# 1. Prerequisites
# ----------------------------------------------------------------------------
if (-not $SkipPrereqs) {
    Write-Step "Checking prerequisites"

    $python = Resolve-Tool "python"
    if (-not $python) {
        Write-Err "Python not on PATH. Install Python 3.13 from python.org (tick 'Add to PATH')."
        exit 2
    }
    $pyVersion = & $python --version 2>&1
    if ($pyVersion -notmatch "3\.1[3-9]") {
        Write-Warn2 "$pyVersion detected. Project targets 3.13+. Continuing but expect breakage."
    } else {
        Write-Ok "$pyVersion"
    }

    $node = Resolve-Tool "node"
    if (-not $node) {
        Write-Err "Node not on PATH. Install Node 20+ from nodejs.org."
        exit 2
    }
    $nodeVersion = & $node --version
    if ($nodeVersion -notmatch "v(2[0-9]|[3-9][0-9])\.") {
        Write-Warn2 "Node $nodeVersion is below 20.x. Expect Next.js 15 incompat."
    } else {
        Write-Ok "Node $nodeVersion"
    }

    $pnpm = Resolve-Tool "pnpm"
    if (-not $pnpm) {
        Write-Step "Enabling corepack and provisioning pnpm"
        & corepack enable | Out-Null
        & corepack prepare pnpm@latest --activate | Out-Null
        $pnpm = Resolve-Tool "pnpm"
        if (-not $pnpm) { Write-Err "corepack failed to provision pnpm"; exit 2 }
    }
    Write-Ok "pnpm $(& $pnpm --version)"
}

# ----------------------------------------------------------------------------
# 2 & 3. Python venv + dep sync
# ($VenvPython is defined at the top of the script so -Diagnose can use it.)
# ----------------------------------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating venv at $VenvDir"
    $python = Resolve-Tool "python"
    & $python -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) {
        Write-Err "venv creation failed"
        exit 4
    }
} else {
    Write-Ok "venv exists, reusing"
}

# Prefer uv for speed if present, fall back to pip. The Day-1 hack: if no
# pyproject.toml exists yet (very fresh clone before Sprint-1 WI-0102 lands),
# bootstrap a single-tier `pip install fastapi uvicorn dramatiq[redis]` so
# the placeholder app boots. Real `uv sync` takes over once pyproject is there.
$pyprojectExists = Test-Path (Join-Path $RepoRoot 'pyproject.toml')

$uv = Resolve-Tool "uv"
if ($uv -and $pyprojectExists) {
    Write-Step "uv sync --all-packages"
    # --all-packages installs every workspace member as editable; bare `uv sync`
    # only installs the root pyproject's deps and leaves the 9 packages + 2 apps
    # missing from .venv. Verified bug Phase 6 round 2026-05-11 (Priya P0).
    & $uv sync --all-packages --python $VenvPython
    if ($LASTEXITCODE -ne 0) { Write-Err "uv sync failed"; exit 4 }
} elseif ($pyprojectExists) {
    Write-Step "pip install -e .[dev] (uv not found, install via 'pip install uv' for 10x speed)"
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { Write-Err "pip install failed"; exit 4 }
} else {
    Write-Warn2 "no pyproject.toml found -- installing Day-1 placeholder deps only"
    & $VenvPython -m pip install --upgrade pip | Out-Null
    & $VenvPython -m pip install "fastapi>=0.115" "uvicorn[standard]>=0.32" `
                                 "dramatiq[redis,watch]>=1.17" "redis>=5.1"
    if ($LASTEXITCODE -ne 0) { Write-Err "placeholder pip install failed"; exit 4 }
}
Write-Ok "Python deps synced"

# ----------------------------------------------------------------------------
# 4. pnpm install
# ----------------------------------------------------------------------------
$webPackageJson = Join-Path $WebDir "package.json"
if (-not (Test-Path $WebDir)) {
    Write-Warn2 "apps/web missing -- skipping frontend bootstrap. Run with -Init to scaffold."
} elseif (-not (Test-Path $webPackageJson)) {
    Write-Warn2 "apps/web exists but has no package.json yet -- Sprint-1 WI-0103 not landed. Skipping pnpm install."
} else {
    Write-Step "pnpm install (apps/web)"
    Push-Location $WebDir
    & $pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "frozen-lockfile failed, retrying without it"
        & $pnpm install
        if ($LASTEXITCODE -ne 0) { Write-Err "pnpm install failed"; Pop-Location; exit 5 }
    }
    Pop-Location
    Write-Ok "pnpm deps synced"
}

# ----------------------------------------------------------------------------
# 5. Redis / Memurai
# ----------------------------------------------------------------------------
if (Test-Port $RedisPort) {
    Write-Ok "Redis-protocol service already listening on :$RedisPort"
} else {
    $memurai = Resolve-Tool "memurai"
    $svc = Get-Service -Name 'Memurai*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($svc) {
        if ($svc.Status -ne 'Running') {
            Write-Step "Starting $($svc.Name) service"
            Start-Service $svc.Name
        }
        Write-Ok "Memurai service running"
    } elseif ($memurai) {
        Write-Step "Starting Memurai"
        Start-Process -FilePath $memurai -WindowStyle Hidden
        Start-Sleep -Milliseconds 800
        if (-not (Test-Port $RedisPort)) {
            Write-Warn2 "Memurai did not bind to :$RedisPort within 800ms. Check the Memurai service."
        } else {
            Write-Ok "Memurai listening"
        }
    } else {
        Write-Err "No Redis-protocol service found. Install Memurai (winget install Memurai.MemuraiDeveloper) or run WSL2 redis-server."
        exit 3
    }
}

# ----------------------------------------------------------------------------
# 5b. Mode-dependent storage substrate (Boris D2 sec11.1 alignment)
# ----------------------------------------------------------------------------
if ($Mode -eq 'm0') {
    Write-Step "Mode M0 -- SQLite + Memurai + local-fs MinIO (no Docker)"
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot 'data') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot 'data/minio-fs') | Out-Null
    $env:OSINT_RUN_MODE         = 'm0'
    $env:OSINT_DB_URL           = "sqlite+aiosqlite:///$RepoRoot/data/evidence.db".Replace('\','/')
    $env:OSINT_SOCK_DB_URL      = "sqlite+aiosqlite:///$RepoRoot/data/sockaccounts.db".Replace('\','/')
    $env:OSINT_MINIO_URL        = "file:///$RepoRoot/data/minio-fs".Replace('\','/')
    $env:OSINT_MINIO_ACCESS_KEY = 'local'
    $env:OSINT_MINIO_SECRET_KEY = 'local'
    $env:OSINT_GRAPH_TIER       = 'none'
} elseif ($Mode -eq 'm1') {
    Write-Step "Mode M1 -- Postgres+AGE + MinIO + Memurai (Docker required)"
    try { & docker info | Out-Null } catch {
        Write-Err "Docker Desktop unreachable. Install Docker Desktop or run -Mode m0."
        exit 8
    }
    $composeFiles = @(
        'infra/compose/docker-compose.yml',
        'infra/compose/docker-compose.dev.yml',
        'infra/compose/docker-compose.win11.yml'
    )
    $missing = $composeFiles | Where-Object { -not (Test-Path (Join-Path $RepoRoot $_)) }
    if ($missing.Count -gt 0) {
        Write-Err "Missing compose files: $($missing -join ', '). Boris's infra/ not landed yet -- fall back to -Mode m0."
        exit 8
    }
    $composeArgs = @()
    foreach ($f in $composeFiles) { $composeArgs += @('-f', $f) }
    & docker compose @composeArgs --profile $GraphTier up -d postgres minio
    if ($LASTEXITCODE -ne 0) { Write-Err "docker compose up failed"; exit 8 }
    $env:OSINT_RUN_MODE         = 'm1'
    $env:OSINT_DB_URL           = 'postgresql+asyncpg://osint:osint@127.0.0.1:5432/osint_evidence'
    $env:OSINT_SOCK_DB_URL      = 'postgresql+asyncpg://osint_sock:osint_sock@127.0.0.1:5433/osint_sockaccounts'
    $env:OSINT_MINIO_URL        = 'http://127.0.0.1:9000'
    $env:OSINT_MINIO_ACCESS_KEY = 'minioadmin'
    $env:OSINT_MINIO_SECRET_KEY = 'minioadmin'
    $env:OSINT_GRAPH_TIER       = $GraphTier
}
$env:OSINT_DEV_MODE   = '1'
$env:OSINT_REDIS_URL  = 'redis://127.0.0.1:6379/0'
$env:OSINT_TOR_SOCKS  = 'socks5h://127.0.0.1:9050'
$env:LOG_LEVEL        = 'DEBUG'
$env:PYTHONUTF8       = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'

# ----------------------------------------------------------------------------
# 6. Spawn dev panes
# ----------------------------------------------------------------------------
# `--reload-dir` is load-bearing on Win11. Without it uvicorn watches CWD,
# which picks up .dev-logs/, .next/, node_modules/, .venv/, data/ writes and
# enters an infinite restart loop (Priya sec7.1). We hand it BOTH the apps/ and
# packages/ directories so edits in either tree fire HMR.
#
# Day-1 paranoia: if neither apps/ nor packages/ exists yet (no -Init and no
# Sprint-1), we degrade to --reload-dir apps with the dirs Init created. We
# never fall back to bare `--reload` -- that's the infinite-restart-loop bomb.
$reloadDirs = @()
foreach ($d in @('apps','packages')) {
    $abs = Join-Path $RepoRoot $d
    if (Test-Path $abs) { $reloadDirs += '--reload-dir'; $reloadDirs += $d }
}
if ($reloadDirs.Count -eq 0) {
    Write-Warn2 "Neither apps/ nor packages/ exists. Run with -Init to scaffold, or expect uvicorn to crash."
    $reloadDirs = @('--reload-dir','apps')  # explicit; never bare --reload
}

$wt = Resolve-Tool "wt"
$reloadDirsStr = ($reloadDirs -join ' ')
$cmdApi    = "`"$VenvPython`" -m uvicorn osint_goblin_api.main:app --reload $reloadDirsStr --port $ApiPort --app-dir apps/api"
# Dramatiq Win11 startup flags:
#   --processes 1 --threads 4   Priya's locked decision; minimizes Win11 process-creation overhead
#                                + dodges the `_winapi.CreateProcess` thread-safety bug (Boris Q3, phase6).
#   --use-spawn                  Defensive against Python 3.14's forkserver default migration.
#                                Win11 has no fork(), so spawn is already required; making it explicit
#                                ensures no silent breakage if the worker is ever invoked under a
#                                Python where the default flips. Boris P0 phase6.
$cmdWorker = "`"$VenvPython`" -m dramatiq osint_goblin_workers --watch apps --processes 1 --threads 4 --use-spawn"
$cmdWeb    = "pnpm --dir `"$WebDir`" dev --port $WebPort"

$apiUp = Test-Port $ApiPort
$webUp = Test-Port $WebPort

if ($apiUp -and $webUp) {
    Write-Ok "API and Web already running -- nothing to spawn"
} elseif ($wt) {
    Write-Step "Launching dev panes in Windows Terminal"
    $wtArgs = @()
    if (-not $apiUp) {
        $wtArgs += "new-tab --title osint-api powershell -NoExit -Command $cmdApi"
        $wtArgs += ";"
        $wtArgs += "split-pane -V --title osint-worker powershell -NoExit -Command $cmdWorker"
    }
    if (-not $webUp) {
        $wtArgs += ";"
        $wtArgs += "split-pane -H --title osint-web powershell -NoExit -Command $cmdWeb"
    }
    Start-Process -FilePath $wt -ArgumentList ($wtArgs -join " ")
    Write-Ok "Spawned"
} else {
    Write-Warn2 "Windows Terminal (wt) not found -- falling back to background jobs"
    if (-not $apiUp) { Start-Job -Name osint-api -ScriptBlock { param($c) Invoke-Expression $c } -ArgumentList $cmdApi | Out-Null }
    if (-not $webUp) { Start-Job -Name osint-web -ScriptBlock { param($c) Invoke-Expression $c } -ArgumentList $cmdWeb | Out-Null }
    Start-Job -Name osint-worker -ScriptBlock { param($c) Invoke-Expression $c } -ArgumentList $cmdWorker | Out-Null
    Write-Warn2 "Logs via 'Receive-Job -Name osint-api -Keep' etc. wt install recommended."
}

# ----------------------------------------------------------------------------
# 7. Health checks
# ----------------------------------------------------------------------------
if ($SkipHealth) { exit 0 }

Write-Step "Waiting for /healthz and / to return 200 (timeout ${HealthTimeoutSeconds}s)"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$apiOk = $false
$webOk = $false
$webPackageExists = Test-Path $webPackageJson  # web is optional during early sprints

while ((Get-Date) -lt $deadline -and (-not ($apiOk -and ($webOk -or -not $webPackageExists)))) {
    if (-not $apiOk) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$ApiPort/healthz" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $apiOk = $true; Write-Ok "FastAPI green" }
        } catch { }
    }
    if (-not $webOk -and $webPackageExists) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$WebPort/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -lt 500) { $webOk = $true; Write-Ok "Next.js green" }
        } catch { }
    }
    Start-Sleep -Milliseconds 700
}

if (-not $apiOk -or ($webPackageExists -and -not $webOk)) {
    Write-Err "Health timeout (api=$apiOk web=$webOk). Check the dev panes for stack traces."
    exit 6
}

if (-not $NoBrowser -and $webOk) { Start-Process "http://localhost:$WebPort/" }
Write-Host ""
Write-Host "Dev stack ready -- close the Windows Terminal panes to stop." -ForegroundColor Green
