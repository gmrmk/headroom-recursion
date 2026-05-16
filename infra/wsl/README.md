# OSINT Goblin -- WSL2 Ubuntu deployment

This is the **canonical Win11 deployment shape** for live recon. Same OPSEC
goals as the Hyper-V path in `../vagrant/` (separate filesystem, separate
kernel namespace, VPN-routable egress, snapshot/reset via `wsl --export`),
without the Hyper-V Default Switch routing drama.

## Why WSL2 over Hyper-V Vagrant

Verified empirically 2026-05-16 on a Win11 Pro host:
- Hyper-V Default Switch dynamically chose mismatched /24 subnets for
  host (172.23.0.1) and VM (172.23.15.1); no route between them.
- generic/debian12 box did not ship working Hyper-V Integration Services
  KVP, so even the Internal-Switch workaround had IP-discovery issues.
- WSL2 networking just works: host can reach `localhost:<port>`,
  WSL filesystem visible at `\\wsl$\Ubuntu\...`.

The Hyper-V Vagrant scaffold remains in `../vagrant/` as the more-isolated
fallback for users on a fresh Win11 install or for adversarial threat
models where shared-kernel namespace is unacceptable.

## Quick start

Prereq: WSL2 + Ubuntu already installed (Win11 typically ships with WSL
disabled but the feature is available; `wsl --install -d Ubuntu` enables it).

Two scripts to run in order:

```powershell
# From a regular PowerShell on Windows. Runs the apt installs as root
# inside WSL (no sudo password prompt because `wsl -u root` bypasses it).
wsl -d Ubuntu -u root -- bash /mnt/c/Users/strid/osint-goblin/infra/wsl/provision_root.sh

# Then as the default user: installs uv, fnm + Node LTS + pnpm, just,
# and clones a Linux-native working copy to ~/workingtitle.
wsl -d Ubuntu -- bash /mnt/c/Users/strid/osint-goblin/infra/wsl/provision_user.sh
```

After both finish:

```powershell
wsl -d Ubuntu
# inside Ubuntu:
cd ~/workingtitle
uv sync --all-packages --all-groups
uv run pytest apps/workers/tests -q
```

You should see `449 passed, 1 skipped` (or whatever the current count is).

## Editing from Windows

Three good options:

1. **VSCode WSL Remote extension** (recommended): `code ~/workingtitle`
   from inside Ubuntu opens the Linux folder with full VSCode features.
   Save speeds match native Linux.
2. **File Explorer**: `\\wsl$\Ubuntu\home\strid\workingtitle\` works in
   any Windows app. Slower for builds because of network-share semantics.
3. **JetBrains Gateway**: similar to VSCode WSL Remote; works well.

## OPSEC posture

- WSL2 has its own Linux kernel + filesystem; investigator's Windows
  C: drive is mounted read-write at `/mnt/c/`. **Do not put target
  data under `/mnt/c/`** — the Hard Passthru module (commit `efe0ff5`)
  enforces this at runtime.
- WireGuard installed via `provision_root.sh`. Configure `/etc/wireguard/wg0.conf`
  with your Mullvad/IVPN profile and `sudo wg-quick up wg0` to route
  all WSL traffic through the VPN.
- `wsl --export Ubuntu C:\osint-snapshot.tar` snapshots the entire
  distro to a tarball. `wsl --import` restores. Use snapshot-restore
  between investigations to drop every trace.

## Re-running

Both scripts are idempotent. Re-run any time to refresh deps after a
sprint. The git clone step skips if `~/workingtitle/.git` already exists;
delete it manually if you want a fresh clone.
