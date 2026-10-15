#!/usr/bin/env bash
# One-time install of NI-DAQmx 2026 Q3 + supporting packages on this PC.
# Run it in a normal terminal window (it asks for a password interactively):
#   sudo bash setup/linux/01_install_ni_daqmx.sh
#
# Notes
# - This PC runs Ubuntu 26.04 (kernel 7.0). NI officially lists only Ubuntu 22.04/24.04,
#   so we use NI's Ubuntu 24.04 ("noble") repository. Dependency resolution was simulated
#   on this machine and the core kernel modules (nikal, nipalk) were test-built against
#   7.0.0-30-generic successfully before writing this script.
# - Secure Boot is enabled, so the DKMS-built NI kernel modules must be signed with a
#   Machine Owner Key (MOK). Step 4 queues that key for enrollment; you confirm it on the
#   blue "MOK Management" screen at the next reboot.
# - Log: /var/log/ni-daqmx-setup.log
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo:  sudo bash $0" >&2
    exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DEB="${HERE}/../../downloads/ni-linux-drivers-2026Q3/NILinux2026Q3DeviceDrivers/ni-ubuntu2404-drivers-2026Q3.deb"
WIRED_IF="${WIRED_IF:-enp6s0}"          # wired port that will go to the TRENDnet switch
LOG=/var/log/ni-daqmx-setup.log
export DEBIAN_FRONTEND=noninteractive   # MOK enrollment is done explicitly in step 4

exec > >(tee -a "${LOG}") 2>&1
echo "##### $(date -Is) NI-DAQmx setup start"

step() { echo; echo "=== $*"; }

step "1/6  Add NI 2026 Q3 package repository (Ubuntu 24.04 build)"
[[ -f "${REPO_DEB}" ]] || { echo "Missing ${REPO_DEB}"; exit 1; }
apt-get install -y "${REPO_DEB}"
apt-get update

step "2/6  Install NI-DAQmx, kernel headers, Python venv/Tk, arp-scan"
apt-get install -y ni-daqmx "linux-headers-$(uname -r)" \
    python3-venv python3-pip python3-tk arp-scan

step "3/6  Build NI kernel modules with DKMS for $(uname -r)"
dkms autoinstall || true
echo "--- dkms status (NI modules):"
dkms status | grep -iE '^ni' || true
bad=$(dkms status -k "$(uname -r)" | grep -iE '^ni' | grep -vc 'installed' || true)
if [[ "${bad}" != "0" ]]; then
    echo "!!! ${bad} NI DKMS module(s) are not installed for $(uname -r). See /var/lib/dkms/*/build/make.log"
fi

step "4/6  Secure Boot: enroll the DKMS signing key (MOK)"
NEED_REBOOT_MOK=0
if mokutil --sb-state 2>/dev/null | grep -q "SecureBoot enabled"; then
    KEY=""
    for k in /var/lib/dkms/mok.pub /var/lib/shim-signed/mok/MOK.der; do
        [[ -f "$k" ]] && { KEY="$k"; break; }
    done
    if [[ -z "${KEY}" ]]; then
        echo "!!! Secure Boot is on but no DKMS signing key was found; NI modules will not load."
    elif mokutil --test-key "${KEY}" 2>&1 | grep -q "already enrolled"; then
        echo "Signing key ${KEY} is already enrolled."
    else
        echo "Queuing ${KEY} for enrollment."
        if [[ -n "${MOK_HASH_FILE:-}" ]]; then   # password pre-hashed with: mokutil --generate-hash=PW
            mokutil --import "${KEY}" --hash-file "${MOK_HASH_FILE}"
        else
            echo ">>> Choose a one-time password now (you'll type it once more after reboot)."
            mokutil --import "${KEY}" </dev/tty >/dev/tty 2>&1
        fi
        NEED_REBOOT_MOK=1
    fi
    echo "nipalk signer: $(modinfo -F signer nipalk 2>/dev/null || echo '?')"
else
    echo "Secure Boot is off; nothing to enroll."
fi

step "5/6  Raise inotify instance limit (NI 2026 Q3 known issue: crash at default 128)"
echo "fs.inotify.max_user_instances = 12288" > /etc/sysctl.d/90-ni-daqmx-inotify.conf
sysctl -p /etc/sysctl.d/90-ni-daqmx-inotify.conf

step "6/6  Firewall: allow inbound traffic on the DAQ port ${WIRED_IF} (needed for chassis discovery)"
if ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow in on "${WIRED_IF}" comment 'NI cDAQ-9191 DAQ network'
    echo "(undo later with: sudo ufw delete allow in on ${WIRED_IF})"
else
    echo "ufw is not active; nothing to do."
fi

echo
echo "##### $(date -Is) NI-DAQmx setup finished"
if [[ ${NEED_REBOOT_MOK} -eq 1 ]]; then
    cat <<EOF

NEXT: reboot now. On the blue "Perform MOK management" screen:
  Enroll MOK -> Continue -> Yes -> type the password you just chose -> Reboot
(If you missed the screen, rerun:  sudo mokutil --import ${KEY:-/var/lib/shim-signed/mok/MOK.der}  and reboot.)
Then run:  bash setup/linux/02_check_install.sh
EOF
else
    echo "NEXT: reboot, then run:  bash setup/linux/02_check_install.sh"
fi
