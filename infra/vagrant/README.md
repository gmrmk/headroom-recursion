# OSINT Goblin -- Investigation VM

This directory contains the Vagrant + Hyper-V config for the **isolated
investigation VM**. The VM is the canonical deployment shape for live
recon: every fetch, every parser, every browser launch happens INSIDE
the VM. The investigator's host machine never touches scraped sites,
never holds target data on disk, never has cookies bleeding between
personal browsing and OSINT work.

## Quick start (Windows 11, Hyper-V path)

Two commands:

```powershell
# 1. From an ELEVATED PowerShell prompt, one-time host setup
#    (enables Hyper-V + installs Vagrant via winget + creates the
#    OSINTInternal switch + assigns host IP 192.168.250.1).
cd <repo-root>\infra\vagrant
.\bootstrap-host.ps1

# 2. (Reboot if step 1 told you to. Then re-run bootstrap-host.ps1
#     so step 3 -- the Internal Switch -- can complete.) Then:
vagrant up
```

First `vagrant up` downloads the Debian 12 box (~500 MB) and runs the
provisioning script. Total wall-clock: 5–15 minutes depending on
bandwidth + disk speed. Subsequent boots are fast.

## Networking

The VM has two virtual NICs:

| NIC | Switch | Purpose | IP |
|---|---|---|---|
| 1 | Default Switch (NAT) | Outbound: apt updates, box downloads, investigation egress (via WireGuard) | DHCP, dynamic |
| 2 | OSINTInternal (Internal) | Host \<-\> VM private link for SSH + Vagrant management | Static `192.168.250.10` |

The host's adapter on `OSINTInternal` is `192.168.250.1`. Use this when
SSH-ing manually: `ssh vagrant@192.168.250.10`. Vagrant uses the same
address.

The Internal Switch sidesteps the Win11 Default Switch routing quirk
where host and VM end up on mismatched `/24` subnets and can't reach
each other.

Once up:

```powershell
vagrant ssh           # drop into a shell inside the VM
cd /vagrant           # the repo is mounted here
just install          # uv sync + pnpm install
just smoke            # health-check the dev stack

# From the host, AFTER you're sure the VM works:
vagrant snapshot save baseline
```

After each investigation, reset everything to clean state:

```powershell
vagrant snapshot restore baseline
```

That drops the entire VM state to the snapshot baseline. Every cookie,
log, body capture, RAM artifact -- gone. The next investigation starts
identity-clean.

## What the VM gives you

| Threat | VM mitigation |
|---|---|
| Investigator's real IP egressing to scraped sites | VM routes through its own VPN profile (WireGuard pre-installed). Configure with your provider's `.conf` file, see "VPN setup" below. |
| Target data persisting on investigator's hard drive | VM disk is ephemeral by snapshot-restore. `/tmp` is tmpfs (RAM-backed) so browser caches + Playwright tempdirs never touch disk. |
| Personal browser cookies mixing with scraping | VM Chromium has zero shared state with the host's Chrome / Edge / Firefox. Cookies in the VM die at snapshot-restore. |
| Anti-bot retaliation flagging the investigator's identity | VM has a separate IP (via VPN) + separate fingerprint (bundled Playwright Chromium). |
| Worker crashes leaving orphaned chromium processes | VM has been provisioned with the cleanup-registry-aware fetcher (`ee1993f`); orphans die when the VM resets. |

## VPN setup (recommended)

The VM ships with `wireguard` + `resolvconf` installed but no profile
configured. To route ALL VM traffic through your VPN:

```bash
# Inside the VM:
sudo nano /etc/wireguard/wg0.conf       # paste your provider's config
sudo wg-quick up wg0                    # start the tunnel
sudo systemctl enable wg-quick@wg0      # auto-start on boot
curl https://ipv4.icanhazip.com         # verify -- should show VPN egress IP
```

Recommended providers (no affiliate; pick based on jurisdiction):
- Mullvad -- WireGuard config generator at https://mullvad.net/account/wireguard-config
- IVPN -- WireGuard configs at https://www.ivpn.net/account/wireguard

Naomi-strict guidance: pay for the VPN with a method that doesn't tie
back to your real identity (Mullvad accepts cash by mail, BTC, etc.).
The VM has a separate IP from your host but if you pay with your real
credit card, the IP-to-investigator linkage still exists at the VPN
provider.

## Tearing down

```powershell
vagrant halt          # stop the VM (preserves state)
vagrant destroy       # remove the VM entirely (drops disk + snapshots)
```

## Troubleshooting

**`vagrant up` fails with "Hyper-V is not enabled"**
Re-run `bootstrap-host.ps1` as Administrator + reboot.

**`vagrant up` is stuck on "Waiting for SSH to be ready"**
First boot can take 2-3 minutes before SSH is up. If it's stuck longer
than 5 min, check Hyper-V Manager -- the VM may have hit the login
prompt. `vagrant halt && vagrant up` usually clears it.

**"Insufficient memory" on Hyper-V**
Edit the `Vagrantfile`: lower `hv.memory` from 4096 to 2048. The VM
still works at 2 GB but Playwright Chromium will be tight.

**Provisioning script errors on `apt-get update`**
Your Debian box may have stale package indices. SSH in and run
`sudo apt-get update --allow-releaseinfo-change` manually.

## What this directory will NOT do

- **Configure your VPN credentials.** Bring your own WireGuard `.conf`.
- **Auto-snapshot before every investigation.** That's manual --
  `vagrant snapshot restore baseline` whenever you want a clean reset.
- **Provide a GUI inside the VM.** This is a headless VM. CAPTCHA
  solving for cookie capture happens in a HEADED Playwright session
  inside the VM (display forwarding via SSH X11 or RDP into the VM if
  you really want a desktop).

## Alternatives if Hyper-V isn't available

- **Windows 11 Home (no Hyper-V):** use WSL2 instead. See main `README.md`
  "WSL2 deployment" section (planned for OPSEC Phase 1.b).
- **Linux/macOS host:** install Vagrant + VirtualBox. The Vagrantfile
  needs a tiny edit (`config.vm.provider "virtualbox"` instead of
  `"hyperv"`). Will land as Phase 1.c.
