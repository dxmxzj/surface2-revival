#!/bin/bash
# Build a bootable FAT32 USB Installer Image for a new Surface 2.
# Reads Wi-Fi credentials from /home/dxm/surface2-wifi.conf (never committed to git) and
# embeds the Golden DTB + Golden Rootfs so booting the USB stick once automatically installs
# Ubuntu 22.04 + the Smart Display Appliance onto the internal 32 GB eMMC (/dev/mmcblk0).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/home/dxm/surface2-backups}"
WIFI_CONF="${WIFI_CONF:-/home/dxm/surface2-wifi.conf}"
SSH_PUBKEY="${SSH_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
OUT_DIR="${1:-/mnt/d/surface2-usb-files}"

if [ ! -f "$WIFI_CONF" ]; then
    echo "ERROR: Wi-Fi config not found at $WIFI_CONF"
    echo "Copy $REPO_DIR/config/surface2-wifi.conf.example to $WIFI_CONF and set WIFI_SSID and WIFI_PASSWORD."
    exit 1
fi

if [ ! -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" ] || [ ! -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" ]; then
    echo "ERROR: Golden backups not found in $BACKUP_DIR."
    echo "Run $REPO_DIR/scripts/capture-golden-backup.sh first."
    exit 1
fi

echo "=== Staging Automated Surface 2 Installer Files to: $OUT_DIR ==="
mkdir -p "$OUT_DIR"

# Stage bootloader and kernel files
cp -f "$REPO_DIR/boot/tegra114-microsoft-surface-2.dtb" "$OUT_DIR/"
cp -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" "$OUT_DIR/"
cp -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" "$OUT_DIR/"

# Wi-Fi & SSH keys
cp -f "$WIFI_CONF" "$OUT_DIR/wpa_supplicant.conf"
if [ -f "$SSH_PUBKEY" ]; then
    cp -f "$SSH_PUBKEY" "$OUT_DIR/authorized_keys"
fi

echo "Staged successfully to $OUT_DIR"
