#!/bin/bash
# Build Fast-Boot Standalone USB Installer Media for Microsoft Surface 2 (RT).
# Enforces the Two-Tier Fast-Boot Architecture:
#   Tier 1: Lean ramdisk (initrd.gz < 50 MB) for instant ~2s UEFI load.
#   Tier 2: External payloads (surface2-rootfs-golden.tar.gz + surface2-boot-golden.tar.gz)
#           staged as separate FAT32 files, mounted dynamically by /init.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/home/dxm/surface2-backups}"
WIFI_CONF="${WIFI_CONF:-/home/dxm/surface2-wifi.conf}"
SSH_PUBKEY="${SSH_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
OUT_DIR="${1:-/mnt/d/surface2-usb-files}"

echo "=== Staging Surface 2 Fast-Boot Media to: $OUT_DIR ==="
mkdir -p "$OUT_DIR"

# Stage Device Tree and Golden Payloads as standalone FAT32 files
cp -f "$REPO_DIR/boot/tegra114-microsoft-surface-2.dtb" "$OUT_DIR/"
if [ -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" ]; then
    cp -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" "$OUT_DIR/"
fi
if [ -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" ]; then
    cp -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" "$OUT_DIR/"
fi

# Wi-Fi & SSH keys
if [ -f "$WIFI_CONF" ]; then
    cp -f "$WIFI_CONF" "$OUT_DIR/wpa_supplicant.conf"
fi
if [ -f "$SSH_PUBKEY" ]; then
    cp -f "$SSH_PUBKEY" "$OUT_DIR/authorized_keys"
fi

# Build and verify the lean ramdisk
echo "Building lean fast-boot initrd.gz..."
python3 "$REPO_DIR/scripts/build_lean_initrd.py"

# Enforce strict size barrier (< 60 MB) to prevent DTB hang regressions
INITRD_SIZE=$(stat -c%s "$OUT_DIR/initrd.gz" 2>/dev/null || stat -f%z "$OUT_DIR/initrd.gz")
MAX_SIZE=$((60 * 1024 * 1024))
if [ "$INITRD_SIZE" -gt "$MAX_SIZE" ]; then
    echo "FATAL: initrd.gz is oversized ($((INITRD_SIZE / 1024 / 1024)) MB > 60 MB)!"
    echo "Monolithic images cause multi-minute UEFI USB 2.0 boot hangs on Surface 2."
    echo "Payloads must remain external on the FAT32 filesystem."
    exit 1
fi

echo "[OK] Fast-Boot media staged successfully ($((INITRD_SIZE / 1024 / 1024)) MB initrd.gz < 60 MB limit)."
