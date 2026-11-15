#!/usr/bin/env python3
"""
Build Lean Fast-Boot initrd.gz (35 MB) for Microsoft Surface 2 (RT).
Loads in ~2 seconds over USB UEFI, then dynamically mounts the USB stick or
MicroSD card to stream the 393 MB rootfs payload onto internal 32 GB eMMC.
"""
import os
import sys
import gzip
import time

SRC_INITRD = '/mnt/d/surface2-usb-files/initrd.gz'
OUT_INITRD = '/mnt/d/surface2-usb-files/initrd.gz'
ONLINE_SH = '/home/dxm/surface2-revival/appliance/surface-online.sh'
WIFI_CONF = '/mnt/d/surface2-usb-files/wpa_supplicant.conf'
SSH_KEY = '/mnt/d/surface2-usb-files/authorized_keys'

LEAN_INIT_SCRIPT = """#!/bin/bash
# Microsoft Surface 2 (RT) Fast-Boot Unattended eMMC Installer
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TERM=linux
export HOME=/root

# Mount fundamental virtual filesystems
mount -t proc proc /proc 2>/dev/null || true
mount -t sysfs sysfs /sys 2>/dev/null || true
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mkdir -p /dev/pts /dev/shm /run /tmp /var/run /var/log /mnt/target /mnt/usb
mount -t devpts devpts /dev/pts 2>/dev/null || true

# Set clock so tar and SSL do not complain about timestamps in the future
date -s "2026-11-10 00:00:00" 2>/dev/null || true

# Force display on and prevent blanking
echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null || true
echo -e "\\033[9;0]" > /dev/tty1 2>/dev/null || true

# Redirect all output directly to the internal screen
exec </dev/tty1 >/dev/tty1 2>&1
clear

echo "=========================================================================="
echo "    Microsoft Surface 2 (RT) — Fast-Boot Ubuntu 22.04 LTS Installer      "
echo "=========================================================================="
echo ""
echo " [*] STANDALONE INSTALLATION. Zero typing / No keyboard required."
echo " [*] Target: Internal 32 GB eMMC (/dev/mmcblk0)"
echo ""

# Search for installer media (USB or MicroSD)
PAYLOAD_DIR=""
echo -n "[*] Scanning for installer media (USB / MicroSD)..."

for attempt in $(seq 1 45); do
    # Check already mounted locations
    for p in /mnt/usb /media /media/* /run/media/* /; do
        if [ -f "$p/surface2-rootfs-golden.tar.gz" ]; then
            PAYLOAD_DIR="$p"
            break 2
        fi
    done

    # Probe all potential block devices (USB /dev/sd* and MicroSD /dev/mmcblk1*)
    for dev in /dev/sda1 /dev/sda /dev/sdb1 /dev/sdb /dev/mmcblk1p1 /dev/mmcblk1 /dev/sd*1 /dev/sd*; do
        [ -b "$dev" ] || continue
        mkdir -p /mnt/usb
        for fstype in vfat msdos ext4 auto; do
            if mount -t "$fstype" -o ro "$dev" /mnt/usb 2>/dev/null; then
                if [ -f "/mnt/usb/surface2-rootfs-golden.tar.gz" ]; then
                    PAYLOAD_DIR="/mnt/usb"
                    echo ""
                    echo "[+] Found installer payload on $dev ($fstype)!"
                    break 3
                fi
                umount /mnt/usb 2>/dev/null || true
            fi
        done
    done

    echo -n "."
    sleep 1
done

echo ""
if [ -z "$PAYLOAD_DIR" ]; then
    echo ""
    echo "=========================================================================="
    echo " [ERROR] Installer payload (surface2-rootfs-golden.tar.gz) NOT FOUND!    "
    echo "=========================================================================="
    echo " Make sure the USB drive or MicroSD card is securely inserted."
    echo ""
    echo " Detected block devices:"
    lsblk 2>/dev/null || cat /proc/partitions
    echo ""
    echo " Dropping to shell for diagnostics..."
    while true; do setsid /bin/bash </dev/tty1 >/dev/tty1 2>&1; sleep 1; done
fi

echo "[1/4] Partitioning internal 32 GB eMMC (/dev/mmcblk0)..."
for part in /dev/mmcblk0p*; do
    umount "$part" 2>/dev/null || true
done

sfdisk --force /dev/mmcblk0 << 'PART_EOF'
label: gpt
unit: sectors
first-lba: 2048
last-lba: 61071326

/dev/mmcblk0p1 : start=2048, size=262144, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="EFI"
/dev/mmcblk0p2 : start=264192, size=60807135, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="Linux"
PART_EOF

sync
sleep 2
partprobe /dev/mmcblk0 2>/dev/null || true
sleep 1

echo "[2/4] Formatting EFI and Root filesystems..."
mkfs.vfat -F 32 -n "EFI" /dev/mmcblk0p1
mkfs.ext4 -F -L "surface-root" /dev/mmcblk0p2

echo "[3/4] Extracting Ubuntu 22.04 LTS Smart Display Rootfs (takes ~90s)..."
mkdir -p /mnt/target
mount -t ext4 /dev/mmcblk0p2 /mnt/target || {
    echo "FATAL: Failed to mount /dev/mmcblk0p2 on /mnt/target!"
    exit 1
}

tar --warning=no-timestamp --numeric-owner -C /mnt/target -xzf "$PAYLOAD_DIR/surface2-rootfs-golden.tar.gz"

mkdir -p /mnt/target/{dev,proc,sys,tmp,run,mnt,boot/efi,etc/wpa_supplicant,root/.ssh,usr/local/bin}
chmod 1777 /mnt/target/tmp
chmod 700 /mnt/target/root/.ssh

cat << 'FSTAB_EOF' > /mnt/target/etc/fstab
/dev/mmcblk0p2  /          ext4  defaults,noatime  0 1
/dev/mmcblk0p1  /boot/efi  vfat  defaults,nofail   0 2
tmpfs           /tmp       tmpfs defaults,nosuid,nodev 0 0
FSTAB_EOF

echo "surface2" > /mnt/target/etc/hostname

# 1. Format and install Wi-Fi configuration cleanly (with robust syntax parser)
WIFI_SRC=""
for w in "$PAYLOAD_DIR/wpa_supplicant.conf" "$PAYLOAD_DIR/surface2-wifi.conf" /wpa_supplicant.conf; do
    if [ -f "$w" ]; then
        WIFI_SRC="$w"
        break
    fi
done

if [ -n "$WIFI_SRC" ]; then
    mkdir -p /mnt/target/etc/wpa_supplicant
    if grep -q "network={" "$WIFI_SRC" 2>/dev/null && grep -q "psk=" "$WIFI_SRC" 2>/dev/null; then
        cp -f "$WIFI_SRC" /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
    else
        SSID=$(grep -iE '^(WIFI_SSID|ssid)=' "$WIFI_SRC" | head -n1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')
        PSK=$(grep -iE '^(WIFI_PASSWORD|password|psk)=' "$WIFI_SRC" | head -n1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')
        if [ -n "$SSID" ] && [ -n "$PSK" ]; then
            cat << WPA_CONF_EOF > /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
ctrl_interface=/run/wpa_supplicant
update_config=1
country=US

network={
    ssid="$SSID"
    psk="$PSK"
    scan_ssid=1
}
WPA_CONF_EOF
        fi
    fi
    chmod 600 /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
fi

# 2. Disable rc.local to eliminate duplicate service runs and console credential leakage
rm -f /mnt/target/etc/rc.local

# 3. Install updated autonomous online daemon
if [ -f "/surface-online.sh" ]; then
    cp -f "/surface-online.sh" /mnt/target/usr/local/bin/surface-online.sh
    chmod 755 /mnt/target/usr/local/bin/surface-online.sh
fi

# 4. Install SSH keys if present
for k in "$PAYLOAD_DIR/authorized_keys" /authorized_keys; do
    if [ -f "$k" ]; then
        cp -f "$k" /mnt/target/root/.ssh/authorized_keys
        chmod 600 /mnt/target/root/.ssh/authorized_keys
        break
    fi
done

# Generate host keys
chroot /mnt/target ssh-keygen -A 2>/dev/null || true

echo "[4/4] Installing UEFI Boot Chain & Golden DTB to /dev/mmcblk0p1..."
mount -t vfat /dev/mmcblk0p1 /mnt/target/boot/efi

BOOT_TAR=""
for b in "$PAYLOAD_DIR/surface2-boot-golden.tar.gz" /surface2-boot-golden.tar.gz; do
    if [ -f "$b" ]; then
        BOOT_TAR="$b"
        break
    fi
done

if [ -n "$BOOT_TAR" ]; then
    tar --warning=no-timestamp -C /mnt/target/boot/efi -xzf "$BOOT_TAR"
fi

if [ -f "$PAYLOAD_DIR/tegra114-microsoft-surface-2.dtb" ]; then
    cp -f "$PAYLOAD_DIR/tegra114-microsoft-surface-2.dtb" /mnt/target/boot/efi/tegra114-microsoft-surface-2.dtb
elif [ -f /tegra114-microsoft-surface-2.dtb ]; then
    cp -f /tegra114-microsoft-surface-2.dtb /mnt/target/boot/efi/tegra114-microsoft-surface-2.dtb
fi

cat << 'CMD_EOF' > /mnt/target/boot/efi/cmdline.txt
dtb=tegra114-microsoft-surface-2.dtb root=/dev/mmcblk0p2 console=tty1 consoleblank=0 loglevel=7 systemd.show_status=true cpuidle.off=1 rootwait rw
CMD_EOF

sync
umount /mnt/target/boot/efi
umount /mnt/target
sync
umount /mnt/usb 2>/dev/null || true
sync

echo ""
echo "=========================================================================="
echo " [SUCCESS] Surface 2 Ubuntu 22.04 LTS Installation COMPLETE!             "
echo "=========================================================================="
echo " - Internal eMMC now contains the permanent Ubuntu 22.04 OS.              "
echo " - Dual 1080p Mirror (DSI + HDMI) and Touchscreen drivers are ready.      "
echo ""
echo " >>> STEP 1: UNPLUG / REMOVE THE USB DRIVE (OR MEDIA) NOW <<<             "
echo ""
echo " >>> STEP 2: Press [ENTER] to reboot (or hold Power button to turn off) <<<"
echo ""

read -r _ </dev/tty1 2>/dev/null || sleep 10

echo "Rebooting into Ubuntu 22.04..."
sync
sleep 1
reboot -f 2>/dev/null || /sbin/reboot -f 2>/dev/null || /usr/sbin/reboot -f 2>/dev/null || echo b > /proc/sysrq-trigger
"""

def make_header(name, size, mode=0o100644, uid=0, gid=0, mtime=1762747200):
    name_bytes = name.encode('utf-8') + b'\x00'
    namesize = len(name_bytes)
    hdr = f'070701{1:08x}{mode:08x}{uid:08x}{gid:08x}{1:08x}{mtime:08x}{size:08x}00000000000000000000000000000000{namesize:08x}00000000'
    res = hdr.encode('ascii') + name_bytes
    pad = (4 - (len(res) % 4)) % 4
    return res + (b'\x00' * pad)

def pad4(n):
    p = (4 - (n % 4)) % 4
    return b'\x00' * p

def build():
    print(f"Reading base image: {SRC_INITRD}...")
    temp_out = OUT_INITRD + ".lean.tmp"
    with gzip.open(SRC_INITRD, 'rb') as f_in, gzip.open(temp_out, 'wb', compresslevel=6) as f_out:
        # Stream existing CPIO files, excluding the massive payload archives
        while True:
            magic = f_in.read(6)
            if not magic or magic != b'070701':
                break
            fields = f_in.read(104)
            namesize = int(fields[88:96], 16)
            filesize = int(fields[48:56], 16)
            name_raw = f_in.read(namesize)
            name = name_raw[:-1].decode('latin1', 'ignore')
            pad = (4 - ((110 + namesize) % 4)) % 4
            f_in.read(pad)

            if name == 'TRAILER!!!':
                f_in.read(filesize)
                pad2 = (4 - (filesize % 4)) % 4
                f_in.read(pad2)
                break

            # EXCLUDE the 400MB payload archives to keep initrd fast (< 35 MB)
            if name in ('surface2-rootfs-golden.tar.gz', 'surface2-boot-golden.tar.gz',
                        'init', 'surface-online.sh', 'usr/local/bin/surface-online.sh', 'wpa_supplicant.conf'):
                f_in.read(filesize)
                pad2 = (4 - (filesize % 4)) % 4
                f_in.read(pad2)
                continue

            # Write existing base entry
            entry_hdr = magic + fields + name_raw + (b'\x00' * pad)
            f_out.write(entry_hdr)
            remaining = filesize
            while remaining > 0:
                chunk = f_in.read(min(remaining, 1048576))
                f_out.write(chunk)
                remaining -= len(chunk)
            pad2 = (4 - (filesize % 4)) % 4
            pad_bytes = f_in.read(pad2)
            f_out.write(pad_bytes)

        # 1. Write updated lean /init script
        print("Injecting fast lean /init installer script...")
        init_data = LEAN_INIT_SCRIPT.encode('utf-8')
        f_out.write(make_header('init', len(init_data), mode=0o100755))
        f_out.write(init_data)
        f_out.write(pad4(len(init_data)))

        # 2. Add extra lightweight scripts
        extra_files = [
            ('surface-online.sh', ONLINE_SH),
            ('usr/local/bin/surface-online.sh', ONLINE_SH),
            ('wpa_supplicant.conf', WIFI_CONF),
            ('authorized_keys', SSH_KEY),
        ]

        for dest_name, src_path in extra_files:
            if not os.path.exists(src_path):
                continue
            sz = os.path.getsize(src_path)
            mode = 0o100755 if dest_name.endswith('.sh') else 0o100600
            print(f"Embedding {dest_name} ({sz} bytes)...")
            f_out.write(make_header(dest_name, sz, mode=mode))
            with open(src_path, 'rb') as f_src:
                while True:
                    chunk = f_src.read(65536)
                    if not chunk:
                        break
                    f_out.write(chunk)
            f_out.write(pad4(sz))

        # 3. Write final TRAILER!!!
        trailer = make_header('TRAILER!!!', 0)
        f_out.write(trailer)

    os.replace(temp_out, OUT_INITRD)
    final_sz = os.path.getsize(OUT_INITRD)
    print(f"[OK] Fast lean initrd generated: {OUT_INITRD} ({final_sz / (1024*1024):.1f} MB)")

if __name__ == '__main__':
    build()
