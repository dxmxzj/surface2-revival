#!/bin/bash
# Build an All-In-One Automated USB Installer for Microsoft Surface 2 (RT).
# Embeds the Golden Rootfs and Bootloader directly into the ramdisk (initrd.gz) so that:
#   1. UEFI loads the entire package over USB (where USB works reliably).
#   2. Linux does not need to access the USB drive after boot (bypassing Tegra 4 USB PHY suspend issues).
#   3. The installer runs 100% hands-free (zero typing, no keyboard, no MicroSD card required).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/home/dxm/surface2-backups}"
WIFI_CONF="${WIFI_CONF:-/home/dxm/surface2-wifi.conf}"
SSH_PUBKEY="${SSH_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
OUT_DIR="${1:-/mnt/d/surface2-usb-files}"

if [ ! -f "$WIFI_CONF" ]; then
    echo "ERROR: Wi-Fi config not found at $WIFI_CONF"
    exit 1
fi

if [ ! -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" ] || [ ! -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" ]; then
    echo "ERROR: Golden backups not found in $BACKUP_DIR."
    exit 1
fi

echo "=== Staging Surface 2 Standalone Installer to: $OUT_DIR ==="
mkdir -p "$OUT_DIR"

cp -f "$REPO_DIR/boot/tegra114-microsoft-surface-2.dtb" "$OUT_DIR/"
cp -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" "$OUT_DIR/"
cp -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" "$OUT_DIR/"
cp -f "$WIFI_CONF" "$OUT_DIR/wpa_supplicant.conf"
if [ -f "$SSH_PUBKEY" ]; then
    cp -f "$SSH_PUBKEY" "$OUT_DIR/authorized_keys"
fi

echo "[OK] Standalone installer staged to $OUT_DIR"
