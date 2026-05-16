# OSINT Goblin -- Windows 11 host bootstrap for the Vagrant VM path.
#
# Purpose: one-time setup on the host machine so the investigator can run
#   `vagrant up`
# in infra/vagrant/ and have the isolated investigation VM come up.
#
# What this does:
#   1. Verifies you are running as Administrator (Hyper-V enable + winget
#      installs both need it).
#   2. Enables the Windows Hyper-V feature if it isn't already enabled.
#   3. Installs Vagrant via winget if it isn't already.
#   4. Tells you whether you need to reboot.
#
# What it does NOT do:
#   - It does NOT run `vagrant up` for you. Run that yourself in
#     infra/vagrant/ once this script is happy.
#   - It does NOT configure your VPN. See README.md "VPN setup" section.
#
# Usage from an ELEVATED PowerShell prompt:
#   cd <repo-root>\infra\vagrant
#   .\bootstrap-host.ps1

#requires -version 5.1

# Step 0: admin check.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
  Write-Host ""
  Write-Host "ERROR: this script must run from an elevated PowerShell prompt." -ForegroundColor Red
  Write-Host "       Right-click PowerShell -> 'Run as administrator', then re-run." -ForegroundColor Red
  Write-Host ""
  exit 1
}

Write-Host ""
Write-Host "OSINT Goblin -- Windows 11 host bootstrap" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Hyper-V feature check + enable if needed.
Write-Host "[1/3] Checking Hyper-V feature state..." -ForegroundColor Yellow
$hyperv = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -ErrorAction SilentlyContinue

$rebootRequired = $false

if ($null -eq $hyperv) {
  Write-Host "      Hyper-V feature is not available on this edition of Windows." -ForegroundColor Red
  Write-Host "      Hyper-V requires Windows 11 Pro / Enterprise / Education." -ForegroundColor Red
  Write-Host "      Home edition users: use the WSL2 path instead (see README.md)." -ForegroundColor Red
  exit 1
}

if ($hyperv.State -eq "Enabled") {
  Write-Host "      Hyper-V is already enabled. OK." -ForegroundColor Green
}
else {
  Write-Host "      Hyper-V is not enabled. Enabling now (no immediate reboot)..." -ForegroundColor Yellow
  $result = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart
  if ($result.RestartNeeded) {
    Write-Host "      Hyper-V enabled. A REBOOT is required before `vagrant up` will work." -ForegroundColor Yellow
    $rebootRequired = $true
  }
  else {
    Write-Host "      Hyper-V enabled. No reboot needed." -ForegroundColor Green
  }
}

# Step 2: Vagrant install via winget.
Write-Host ""
Write-Host "[2/3] Checking Vagrant install..." -ForegroundColor Yellow
$vagrant = Get-Command vagrant -ErrorAction SilentlyContinue

if ($null -ne $vagrant) {
  $version = (vagrant --version 2>$null)
  Write-Host "      Vagrant is already installed: $version" -ForegroundColor Green
}
else {
  Write-Host "      Vagrant not found. Installing via winget..." -ForegroundColor Yellow
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($null -eq $winget) {
    Write-Host "      ERROR: winget is not available. Install App Installer from the" -ForegroundColor Red
    Write-Host "             Microsoft Store, then re-run this script." -ForegroundColor Red
    exit 1
  }
  winget install --silent --accept-source-agreements --accept-package-agreements Hashicorp.Vagrant
  Write-Host "      Vagrant installed. You may need to open a new PowerShell" -ForegroundColor Green
  Write-Host "      session for the PATH update to take effect." -ForegroundColor Green
}

# Step 3: final status.
Write-Host ""
Write-Host "[3/3] Bootstrap complete." -ForegroundColor Cyan
Write-Host ""

if ($rebootRequired) {
  Write-Host "ACTION REQUIRED:" -ForegroundColor Yellow
  Write-Host "  1. Reboot this machine (Hyper-V activation needs it)." -ForegroundColor Yellow
  Write-Host "  2. After reboot, open a new PowerShell, cd to infra\vagrant," -ForegroundColor Yellow
  Write-Host "     and run 'vagrant up'." -ForegroundColor Yellow
}
else {
  Write-Host "NEXT STEP:" -ForegroundColor Green
  Write-Host "  cd $PSScriptRoot" -ForegroundColor Green
  Write-Host "  vagrant up" -ForegroundColor Green
  Write-Host ""
  Write-Host "First boot downloads ~500MB Debian 12 image then provisions" -ForegroundColor Green
  Write-Host "the VM (~5-15 min depending on bandwidth). Subsequent boots" -ForegroundColor Green
  Write-Host "are fast." -ForegroundColor Green
}

Write-Host ""
