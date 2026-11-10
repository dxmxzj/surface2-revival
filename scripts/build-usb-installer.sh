#!/bin/bash
# Build a bootable FAT32 USB Installer Image (surface2-ubuntu22-installer.img) for a new Surface 2.
# Reads Wi-Fi credentials from /home/dxm/surface2-wifi.conf (never committed to git) and
# embeds the Golden DTB + Golden Rootfs so booting the USB stick once automatically installs
# Ubuntu 22.04 + the Smart Display Appliance onto the internal 32 GB eMMC (/dev/mmcblk0).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/home/dxm/surface2-backups}"
WIFI_CONF="${WIFI_CONF:-/home/dxm/surface2-wifi.conf}"
SSH_PUBKEY="${SSH_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
OUT_IMG="${1:-$BACKUP_DIR/surface2-ubuntu22-installer.img}"

if [ ! -f "$WIFI_CONF" ]; then
    echo "ERROR: Wi-Fi config not found at $WIFI_CONF"
    echo "Copy $REPO_DIR/config/surface2-wifi.conf.example to $WIFI_CONF and set WIFI_SSID and WIFI_PASSWORD."
    exit 1
fi
source "$WIFI_CONF"

if [ ! -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" ] || [ ! -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" ]; then
    echo "ERROR: Golden backups not found in $BACKUP_DIR."
    echo "Run $REPO_DIR/scripts/capture-golden-backup.sh first."
    exit 1
fi

echo "=== Building Surface 2 Automated USB-to-eMMC Installer Image: $OUT_IMG ==="
WORK_DIR=$(mktemp -d /tmp/s2-installer-build.XXXXXX)
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$WORK_DIR/boot" "$WORK_DIR/initrd"
tar -C "$WORK_DIR/boot" -xzf "$BACKUP_DIR/surface2-boot-golden.tar.gz"
cp -f "$REPO_DIR/boot/tegra114-microsoft-surface-2.dtb" "$WORK_DIR/boot/tegra114-microsoft-surface-2.dtb"
echo "dtb=tegra114-microsoft-surface-2.dtb initrd=initrd.gz console=tty1 consoleblank=0 loglevel=7 cpuidle.off=1 rw" > "$WORK_DIR/boot/cmdline.txt"

# Extract base initrd and add automated eMMC installer /init
tar -C "$WORK_DIR/initrd" -xzf "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" \
    ./bin ./sbin ./usr/bin ./usr/sbin ./lib ./etc ./usr/lib 2>/dev/null || true
mkdir -p "$WORK_DIR/initrd"/{proc,sys,dev,run,tmp,mnt/usb,mnt/p1,mnt/p2}
cp -f "$REPO_DIR/boot/tegra114-microsoft-surface-2.dtb" "$WORK_DIR/initrd/golden.dtb"

cat << 'INIT_EOF' > "$WORK_DIR/initrd/init"
#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mount -t proc proc /proc 2>/dev/null || true
mount -t sysfs sysfs /sys 2>/dev/null || true
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mkdir -p /dev/pts /run /tmp /mnt/usb /mnt/p1 /mnt/p2
mount -t devpts devpts /dev/pts 2>/dev/null || true

exec </dev/tty1 >/dev/tty1 2>&1
clear
echo "========================================================================"
echo "  Microsoft Surface 2 (Tegra 4) — Automated Ubuntu 22.04 eMMC Installer"
echo "========================================================================"

echo "[1/5] Locating USB installer media..."
USB_DEV=""
for iter in $(seq 1 20); do
    for dev in /dev/sd*1 /dev/sd*; do
        [ -b "$dev" ] || continue
        if mount -o ro "$dev" /mnt/usb 2>/dev/null; then
            if [ -f /mnt/usb/surface2-rootfs-golden.tar.gz ]; then
                USB_DEV="$dev"
                break 2
            fi
            umount /mnt/usb 2>/dev/null || true
        fi
    done
    sleep 1
done

if [ -z "$USB_DEV" ]; then
    echo "ERROR: Could not find surface2-rootfs-golden.tar.gz on USB drive!"
    exec /bin/bash
fi
echo "      Found installer payload on $USB_DEV"

echo "[2/5] Partitioning internal 32 GB eMMC (/dev/mmcblk0)..."
umount /dev/mmcblk0p* 2>/dev/null || true
parted -s /dev/mmcblk0 mklabel gpt
parted -s /dev/mmcblk0 mkpart ESP fat32 1MiB 129MiB
parted -s /dev/mmcblk0 set 1 esp on
parted -s /dev/mmcblk0 mkpart rootfs ext4 129MiB 100%
partprobe /dev/mmcblk0 2>/dev/null || true
sleep 2

mkfs.vfat -F 32 -n S2BOOT /dev/mmcblk0p1
mkfs.ext4 -F -L S2ROOT /dev/mmcblk0p2

echo "[3/5] Installing UEFI Boot Chain & Golden DTB to /dev/mmcblk0p1..."
mount /dev/mmcblk0p1 /mnt/p1
tar -C /mnt/p1 -xzf /mnt/usb/surface2-boot-golden.tar.gz
cp -f /golden.dtb /mnt/p1/tegra114-microsoft-surface-2.dtb
echo "dtb=tegra114-microsoft-surface-2.dtb root=/dev/mmcblk0p2 console=tty1 consoleblank=0 loglevel=7 systemd.show_status=true cpuidle.off=1 rootwait rw" > /mnt/p1/cmdline.txt
sync
umount /mnt/p1

echo "[4/5] Extracting Ubuntu 22.04 Smart Display Rootfs to /dev/mmcblk0p2 (takes ~90s)..."
mount /dev/mmcblk0p2 /mnt/p2
tar -C /mnt/p2 -xzf /mnt/usb/surface2-rootfs-golden.tar.gz
mkdir -p /mnt/p2/{proc,sys,dev,run,tmp,mnt,boot/efi}
chmod 1777 /mnt/p2/tmp

if [ -f /mnt/usb/wpa_supplicant.conf ]; then
    mkdir -p /mnt/p2/etc/wpa_supplicant
    cp -f /mnt/usb/wpa_supplicant.conf /mnt/p2/etc/wpa_supplicant/wpa_supplicant.conf
    chmod 600 /mnt/p2/etc/wpa_supplicant/wpa_supplicant.conf
fi
if [ -f /mnt/usb/authorized_keys ]; then
    mkdir -p /mnt/p2/root/.ssh
    cp -f /mnt/usb/authorized_keys /mnt/p2/root/.ssh/authorized_keys
    chmod 700 /mnt/p2/root/.ssh
    chmod 600 /mnt/p2/root/.ssh/authorized_keys
fi
chroot /mnt/p2 ssh-keygen -A 2>/dev/null || true
sync
umount /mnt/p2
umount /mnt/usb 2>/dev/null || true

echo "[5/5] Installation Complete! Unplug USB drive if using physical stick."
echo "      Triggering 5-second hardware watchdog reboot into internal eMMC..."
sync
python3 -c '
import fcntl, os, struct, time
fd = os.open("/dev/watchdog0", os.O_RDWR)
fcntl.ioctl(fd, 0xc0045706, struct.pack("I", 5))
time.sleep(10)
'
INIT_EOF
chmod +x "$WORK_DIR/initrd/init"

echo "Building initrd.gz..."
(cd "$WORK_DIR/initrd" && find . -print0 | cpio --null -ov --format=newc 2>/dev/null | gzip -1 > "$WORK_DIR/boot/initrd.gz")

cp -f "$BACKUP_DIR/surface2-boot-golden.tar.gz" "$WORK_DIR/boot/"
cp -f "$BACKUP_DIR/surface2-rootfs-golden.tar.gz" "$WORK_DIR/boot/"

cat << EOF > "$WORK_DIR/boot/wpa_supplicant.conf"
ctrl_interface=/run/wpa_supplicant
update_config=1
country=US

network={
    ssid="$WIFI_SSID"
    psk="$WIFI_PASSWORD"
    scan_ssid=1
}
EOF
[ -f "$SSH_PUBKEY" ] && cp -f "$SSH_PUBKEY" "$WORK_DIR/boot/authorized_keys"

IMG_SIZE_MB=$(du -sm "$WORK_DIR/boot" | awk '{print $1 + 128}')
dd if=/dev/zero of="$OUT_IMG" bs=1M count="$IMG_SIZE_MB" status=progress
mkfs.vfat -F 32 -n S2INSTALL "$OUT_IMG"
mcopy -i "$OUT_IMG" -s "$WORK_DIR/boot/"* ::/
chmod 600 "$OUT_IMG"

echo "=== Built Bootable USB Installer Image: $OUT_IMG (${IMG_SIZE_MB} MB) ==="
