#!/bin/bash
# Microsoft Surface 2 (Tegra 4) Automated Ubuntu 22.04 LTS eMMC Installer
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TERM=linux
export HOME=/root
set -e
exec </dev/tty1 >/dev/tty1 2>&1
clear

# Set clock so tar and SSL do not complain about timestamps in the future
date -s "2026-11-10 00:00:00" 2>/dev/null || true

echo "=========================================================================="
echo "    Microsoft Surface 2 (Tegra 4) — Automated Ubuntu 22.04 eMMC Installer "
echo "=========================================================================="
echo ""
echo "[*] Target device: /dev/mmcblk0 (Internal 32GB eMMC Flash)"
echo ""

# Find where this installer is mounted (USB or live disk)
USB_DIR=""
for p in /mnt /mnt/usb /media /media/* /run/media/* /; do
    if [ -f "$p/surface2-rootfs-golden.tar.gz" ]; then
        USB_DIR="$p"
        break
    fi
done

if [ -z "$USB_DIR" ]; then
    for dev in /dev/sd*1 /dev/sd* /dev/mmcblk1p* /dev/mmcblk1; do
        [ -b "$dev" ] || continue
        mkdir -p /mnt/usb
        if mount -o ro "$dev" /mnt/usb 2>/dev/null; then
            if [ -f /mnt/usb/surface2-rootfs-golden.tar.gz ]; then
                USB_DIR="/mnt/usb"
                break
            fi
            umount /mnt/usb 2>/dev/null || true
        fi
    done
fi

if [ -z "$USB_DIR" ]; then
    echo "ERROR: Could not locate surface2-rootfs-golden.tar.gz payload!"
    exit 1
fi
echo "[+] Found installer payload in $USB_DIR"

echo "[1/4] Partitioning internal 32 GB eMMC (/dev/mmcblk0)..."
for part in /dev/mmcblk0p*; do
    umount "$part" 2>/dev/null || true
done

sfdisk --force /dev/mmcblk0 << 'PART_EOF'
label: gpt
size=128M, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="EFI"
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="Linux"
PART_EOF

sleep 2
partprobe /dev/mmcblk0 2>/dev/null || true

echo "[2/4] Formatting internal partitions..."
mkfs.fat -F 32 -n "EFI" /dev/mmcblk0p1 || mkdosfs -F 32 /dev/mmcblk0p1
mkfs.ext4 -F -L "surface-root" /dev/mmcblk0p2

echo "[3/4] Installing Ubuntu 22.04 LTS Smart Display Rootfs (takes ~90s)..."
mkdir -p /mnt/target
mount -t ext4 /dev/mmcblk0p2 /mnt/target
tar --warning=no-timestamp --numeric-owner -C /mnt/target -xzf "$USB_DIR/surface2-rootfs-golden.tar.gz"

mkdir -p /mnt/target/{dev,proc,sys,tmp,run,mnt,boot/efi,etc/wpa_supplicant,root/.ssh}
chmod 1777 /mnt/target/tmp
chmod 700 /mnt/target/root/.ssh

# Configure fstab
cat << 'FSTAB_EOF' > /mnt/target/etc/fstab
# /etc/fstab: Microsoft Surface 2 eMMC mount table
/dev/mmcblk0p2  /           ext4    defaults,noatime    0   1
/dev/mmcblk0p1  /boot/efi   vfat    defaults,noatime    0   2
tmpfs           /tmp        tmpfs   defaults,nosuid,nodev 0 0
FSTAB_EOF

echo "surface2" > /mnt/target/etc/hostname

# Install Wi-Fi & SSH keys
if [ -f "$USB_DIR/wpa_supplicant.conf" ]; then
    cp -f "$USB_DIR/wpa_supplicant.conf" /mnt/target/etc/wpa_supplicant/
    chmod 600 /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
fi

if [ -f "$USB_DIR/authorized_keys" ]; then
    cp -f "$USB_DIR/authorized_keys" /mnt/target/root/.ssh/
    chmod 600 /mnt/target/root/.ssh/authorized_keys
fi

echo "[4/4] Installing UEFI bootloader & Golden DTB to internal ESP..."
mount /dev/mmcblk0p1 /mnt/target/boot/efi
tar -C /mnt/target/boot/efi -xzf "$USB_DIR/surface2-boot-golden.tar.gz"

cat << 'CMD_EOF' > /mnt/target/boot/efi/cmdline.txt
dtb=tegra114-microsoft-surface-2.dtb root=/dev/mmcblk0p2 console=tty1 consoleblank=0 loglevel=7 systemd.show_status=true cpuidle.off=1 rootwait rw
CMD_EOF

sync
umount /mnt/target/boot/efi
umount /mnt/target
sync

echo ""
echo "=========================================================="
echo " [SUCCESS] Surface 2 Linux Installation is COMPLETE!     "
echo "=========================================================="
echo " - Windows RT has been replaced on the internal eMMC."
echo " - Linux is now the permanent standalone operating system."
echo " - Wi-Fi, SSH, Touchscreen, and 1080p Mirror are ready."
echo ""
echo " >>> STEP 1: UNPLUG / REMOVE THE USB DRIVE NOW <<<"
echo " >>> STEP 2: Press [ENTER] to reboot <<<"
echo ""
read -r _ </dev/tty1 2>/dev/null || sleep 10

echo "Rebooting into Ubuntu..."
sync
sleep 1
reboot -f 2>/dev/null || /sbin/reboot -f 2>/dev/null || /usr/sbin/reboot -f 2>/dev/null || echo b > /proc/sysrq-trigger
