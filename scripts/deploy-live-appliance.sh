#!/bin/bash
# Deploy or update the Surface 2 Smart Display Appliance stack over SSH to a live Surface 2
# Usage: ./deploy-live-appliance.sh [root@surface2-ip] [ssh-jump-host]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-root@192.168.86.76}"
JUMP="${2:-root@glkvm-ai-pc.tail693f4.ts.net}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
if [ -n "$JUMP" ]; then
    SSH_OPTS+=(-J "$JUMP")
fi

echo "=== Deploying Appliance Scripts & Systemd Units to $TARGET ==="
scp "${SSH_OPTS[@]}" "$REPO_DIR/appliance/"* "$TARGET:/usr/local/bin/"
scp "${SSH_OPTS[@]}" \
    "$REPO_DIR/systemd/surface-wifi.service" \
    "$REPO_DIR/systemd/surface-dashboard.service" \
    "$REPO_DIR/systemd/surface-touch-kbd.service" \
    "$REPO_DIR/systemd/surface-touch-init.service" \
    "$REPO_DIR/systemd/uxplay.service" \
    "$TARGET:/etc/systemd/system/"
scp "${SSH_OPTS[@]}" "$REPO_DIR/systemd/autologin.conf" "$TARGET:/etc/systemd/system/getty@tty1.service.d/autologin.conf"
scp "${SSH_OPTS[@]}" "$REPO_DIR/boot/tegra114-microsoft-surface-2.dtb" "$TARGET:/tmp/tegra114-microsoft-surface-2.dtb"

ssh "${SSH_OPTS[@]}" "$TARGET" "
chmod +x /usr/local/bin/surface2_smart_receiver.py /usr/local/bin/surface-touch-kbd.py /usr/local/bin/surface-touch-init.py /usr/local/bin/surface-hdmi-phy.py /usr/local/bin/dashboard /usr/local/bin/cast /usr/local/bin/fast-reboot /usr/local/bin/surface-online.sh
ln -sf /usr/local/bin/dashboard /usr/local/bin/start-dashboard
ln -sf /usr/local/bin/dashboard /usr/local/bin/stop-dashboard
mount /dev/mmcblk0p1 /boot/efi 2>/dev/null || true
cp -f /tmp/tegra114-microsoft-surface-2.dtb /boot/efi/tegra114-microsoft-surface-2.dtb
sed -i 's/^#RuntimeWatchdogSec=.*/RuntimeWatchdogSec=30s/' /etc/systemd/system.conf
sed -i 's/^#RebootWatchdogSec=.*/RebootWatchdogSec=5s/' /etc/systemd/system.conf
systemctl daemon-reload
systemctl enable surface-touch-init.service surface-wifi.service surface-dashboard.service uxplay.service
systemctl restart surface-dashboard.service
sync
"
echo "=== Deployment Complete on $TARGET ==="
