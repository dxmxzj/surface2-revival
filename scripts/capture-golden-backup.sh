#!/bin/bash
# Capture a clean, secret-free Golden Backup of /boot/efi and / from a running Surface 2
# Usage: ./capture-golden-backup.sh [user@surface2-ip] [ssh-jump-host]
set -euo pipefail

TARGET="${1:-root@192.168.86.76}"
JUMP="${2:-root@glkvm-ai-pc.tail693f4.ts.net}"
BACKUP_DIR="${BACKUP_DIR:-/home/dxm/surface2-backups}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
if [ -n "$JUMP" ]; then
    SSH_OPTS+=(-J "$JUMP")
fi

echo "=== [1/2] Capturing /boot/efi partition from $TARGET ==="
ssh "${SSH_OPTS[@]}" "$TARGET" "mount /dev/mmcblk0p1 /boot/efi 2>/dev/null || true; tar -C /boot/efi -czf - ." \
    > "$BACKUP_DIR/surface2-boot-golden.tar.gz"
ls -lh "$BACKUP_DIR/surface2-boot-golden.tar.gz"

echo "=== [2/2] Capturing sanitized Ubuntu 22.04 rootfs (/dev/mmcblk0p2) from $TARGET ==="
echo "    (Excludes Wi-Fi credentials, SSH host keys, and Tailscale node state so each tablet gets a clean identity)"
ssh "${SSH_OPTS[@]}" "$TARGET" "tar --one-file-system -C / \
    --exclude='./proc/*' \
    --exclude='./sys/*' \
    --exclude='./dev/*' \
    --exclude='./run/*' \
    --exclude='./tmp/*' \
    --exclude='./mnt/*' \
    --exclude='./var/cache/apt/archives/*.deb' \
    --exclude='./etc/wpa_supplicant/wpa_supplicant.conf' \
    --exclude='./var/lib/tailscale/tailscaled.state' \
    --exclude='./etc/ssh/ssh_host_*' \
    -czf - ." > "$BACKUP_DIR/surface2-rootfs-golden.tar.gz"

ls -lh "$BACKUP_DIR/surface2-rootfs-golden.tar.gz"
echo "=== Golden Backup Complete in $BACKUP_DIR ==="
