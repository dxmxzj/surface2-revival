#!/usr/bin/env python3
"""
Build standalone all-in-one initrd.gz for Microsoft Surface 2 (RT).
Embeds the rootfs and bootloader golden backups directly in RAM so that:
1. UEFI loads the full environment over USB.
2. The installation is 100% independent of Tegra 4 USB PHY driver suspend bugs.
3. Zero typing / hands-free unattended installation to internal 32GB eMMC.
4. Robust Wi-Fi configuration generator (handles key-value and wpa_supplicant syntax).
5. Disables rc.local to eliminate credential exposure on console.
6. Continuous dynamic Wi-Fi interface detection and power-save management.
"""
import os
import sys
import gzip
import io
import time

SRC_INITRD = '/mnt/d/surface2-usb-files/initrd-lean.gz' if os.path.exists('/mnt/d/surface2-usb-files/initrd-lean.gz') else '/mnt/d/surface2-usb-files/initrd.gz'
ROOTFS_TAR = '/mnt/d/surface2-usb-files/surface2-rootfs-golden.tar.gz'
BOOT_TAR = '/mnt/d/surface2-usb-files/surface2-boot-golden.tar.gz'
DTB_FILE = '/mnt/d/surface2-usb-files/tegra114-microsoft-surface-2.dtb'
WIFI_CONF = '/mnt/d/surface2-usb-files/wpa_supplicant.conf'
SSH_KEY = '/mnt/d/surface2-usb-files/authorized_keys'
ONLINE_SH = '/home/dxm/surface2-revival/appliance/surface-online.sh'
RECEIVER_PY = '/home/dxm/surface2-revival/appliance/surface2_smart_receiver.py'
OUT_INITRD = '/mnt/d/surface2-usb-files/initrd.gz'

INIT_SCRIPT = """#!/bin/bash
# Microsoft Surface 2 (RT) Automated Ubuntu 22.04 LTS eMMC Installer
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
echo "    Microsoft Surface 2 (RT) — Automated Ubuntu 22.04 LTS Installer      "
echo "=========================================================================="
echo ""
echo " [*] STANDALONE INSTALLATION. Zero typing / No keyboard required."
echo " [*] Target: Internal 32 GB eMMC (/dev/mmcblk0)"
echo ""

# Check for embedded payload
if [ ! -f "/surface2-rootfs-golden.tar.gz" ]; then
    echo "[ERROR] Embedded installer payload not found in RAM!"
    while true; do setsid /bin/bash </dev/tty1 >/dev/tty1 2>&1; sleep 1; done
fi

# Check for existing Linux installation on /dev/mmcblk0p2 (smart config preservation)
EXISTING_INSTALL=0
mkdir -p /mnt/check
if mount -t ext4 -o ro /dev/mmcblk0p2 /mnt/check 2>/dev/null; then
    if [ -f /mnt/check/etc/os-release ]; then
        EXISTING_INSTALL=1
    fi
    umount /mnt/check 2>/dev/null || true
fi

mkdir -p /tmp/preserved_config

if [ "$EXISTING_INSTALL" -eq 1 ]; then
    echo "=========================================================================="
    echo " [*] EXISTING LINUX INSTALLATION DETECTED ON /dev/mmcblk0p2!              "
    echo " [*] PRESERVING CONFIGURATION: Tailscale, Wi-Fi, SSH keys, Hostname       "
    echo "=========================================================================="
    mkdir -p /mnt/target
    mount -t ext4 /dev/mmcblk0p2 /mnt/target 2>/dev/null || true
    if [ -d /mnt/target/var/lib/tailscale ]; then
        echo " [+] Preserving Tailscale machine identity & login..."
        mkdir -p /tmp/preserved_config/tailscale
        cp -a /mnt/target/var/lib/tailscale/* /tmp/preserved_config/tailscale/ 2>/dev/null || true
    fi
    if [ -f /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf ]; then
        echo " [+] Preserving existing Wi-Fi configuration..."
        mkdir -p /tmp/preserved_config/wpa_supplicant
        cp -a /mnt/target/etc/wpa_supplicant/* /tmp/preserved_config/wpa_supplicant/ 2>/dev/null || true
    fi
    if [ -d /mnt/target/etc/ssh ]; then
        echo " [+] Preserving SSH host keys..."
        mkdir -p /tmp/preserved_config/etc_ssh
        cp -a /mnt/target/etc/ssh/ssh_host_* /tmp/preserved_config/etc_ssh/ 2>/dev/null || true
    fi
    if [ -f /mnt/target/root/.ssh/authorized_keys ]; then
        mkdir -p /tmp/preserved_config/root_ssh
        cp -a /mnt/target/root/.ssh/* /tmp/preserved_config/root_ssh/ 2>/dev/null || true
    fi
    if [ -f /mnt/target/etc/hostname ]; then
        cp -a /mnt/target/etc/hostname /tmp/preserved_config/
    fi
    umount /mnt/target 2>/dev/null || true
    echo ""
else
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
fi

echo "[3/4] Extracting Ubuntu 22.04 LTS Smart Display Rootfs (takes ~90s)..."
mkdir -p /mnt/target
mount -t ext4 /dev/mmcblk0p2 /mnt/target || {
    echo "FATAL: Failed to mount /dev/mmcblk0p2 on /mnt/target!"
    exit 1
}

tar --warning=no-timestamp --numeric-owner -C /mnt/target -xzf /surface2-rootfs-golden.tar.gz

mkdir -p /mnt/target/{dev,proc,sys,tmp,run,mnt,boot/efi,etc/wpa_supplicant,root/.ssh,usr/local/bin}
chmod 1777 /mnt/target/tmp
chmod 700 /mnt/target/root/.ssh

cat << 'FSTAB_EOF' > /mnt/target/etc/fstab
/dev/mmcblk0p2  /          ext4  defaults,noatime  0 1
/dev/mmcblk0p1  /boot/efi  vfat  defaults,nofail   0 2
tmpfs           /tmp       tmpfs defaults,nosuid,nodev 0 0
FSTAB_EOF

echo "surface2" > /mnt/target/etc/hostname

# 1. Restore Tailscale credentials if preserved
if [ -d /tmp/preserved_config/tailscale ] && [ "$(ls -A /tmp/preserved_config/tailscale 2>/dev/null)" ]; then
    echo " [+] Restoring preserved Tailscale authentication..."
    mkdir -p /mnt/target/var/lib/tailscale
    cp -a /tmp/preserved_config/tailscale/* /mnt/target/var/lib/tailscale/
    chmod 600 /mnt/target/var/lib/tailscale/* 2>/dev/null || true
fi

# 2. Restore or install Wi-Fi configuration
if [ -d /tmp/preserved_config/wpa_supplicant ] && [ -f /tmp/preserved_config/wpa_supplicant/wpa_supplicant.conf ]; then
    echo " [+] Restoring preserved Wi-Fi network settings..."
    cp -a /tmp/preserved_config/wpa_supplicant/* /mnt/target/etc/wpa_supplicant/
    chmod 600 /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
elif [ -f "/wpa_supplicant.conf" ]; then
    mkdir -p /mnt/target/etc/wpa_supplicant
    if grep -q "network={" /wpa_supplicant.conf 2>/dev/null && grep -q "psk=" /wpa_supplicant.conf 2>/dev/null; then
        cp -f "/wpa_supplicant.conf" /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
    else
        SSID=$(grep -iE '^(WIFI_SSID|ssid)=' /wpa_supplicant.conf | head -n1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')
        PSK=$(grep -iE '^(WIFI_PASSWORD|password|psk)=' /wpa_supplicant.conf | head -n1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')
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

# 3. Disable rc.local to eliminate duplicate service runs and console credential leakage
rm -f /mnt/target/etc/rc.local

# 3b. Disable sleep/suspend/hibernate and ignore lid/power-key half-states on Tegra 4
mkdir -p /mnt/target/etc/systemd/logind.conf.d
cat << 'LID_EOF' > /mnt/target/etc/systemd/logind.conf.d/ignore-lid.conf
[Login]
HandlePowerKey=ignore
HandlePowerKeyLongPress=poweroff
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
LID_EOF
chroot /mnt/target systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target 2>/dev/null || true

mkdir -p /mnt/target/etc/systemd/system.conf.d
cat << 'STOP_EOF' > /mnt/target/etc/systemd/system.conf.d/fast-shutdown.conf
[Manager]
DefaultTimeoutStopSec=5s
STOP_EOF

# 4. Install updated autonomous online daemon & smart receiver
if [ -f "/surface-online.sh" ]; then
    cp -f "/surface-online.sh" /mnt/target/usr/local/bin/surface-online.sh
    chmod 755 /mnt/target/usr/local/bin/surface-online.sh
fi
if [ -f "/surface2_smart_receiver.py" ]; then
    cp -f "/surface2_smart_receiver.py" /mnt/target/usr/local/bin/surface2_smart_receiver.py
    chmod 755 /mnt/target/usr/local/bin/surface2_smart_receiver.py
fi

if [ -d /tmp/preserved_config/root_ssh ] && [ -f /tmp/preserved_config/root_ssh/authorized_keys ]; then
    cp -a /tmp/preserved_config/root_ssh/* /mnt/target/root/.ssh/
    chmod 600 /mnt/target/root/.ssh/authorized_keys
elif [ -f "/authorized_keys" ]; then
    cp -f "/authorized_keys" /mnt/target/root/.ssh/
    chmod 600 /mnt/target/root/.ssh/authorized_keys
fi

if [ -f /tmp/preserved_config/hostname ]; then
    cp -a /tmp/preserved_config/hostname /mnt/target/etc/hostname
fi

# 5. Restore or generate SSH host keys
if [ -d /tmp/preserved_config/etc_ssh ] && [ "$(ls -A /tmp/preserved_config/etc_ssh 2>/dev/null)" ]; then
    echo " [+] Restoring preserved SSH host keys..."
    cp -a /tmp/preserved_config/etc_ssh/* /mnt/target/etc/ssh/
    chmod 600 /mnt/target/etc/ssh/ssh_host_*_key 2>/dev/null || true
    chmod 644 /mnt/target/etc/ssh/ssh_host_*_key.pub 2>/dev/null || true
else
    chroot /mnt/target ssh-keygen -A 2>/dev/null || true
fi

echo "[4/4] Installing UEFI Boot Chain & Golden DTB to /dev/mmcblk0p1..."
mount -t vfat /dev/mmcblk0p1 /mnt/target/boot/efi
tar --warning=no-timestamp -C /mnt/target/boot/efi -xzf /surface2-boot-golden.tar.gz

if [ -f /tegra114-microsoft-surface-2.dtb ]; then
    cp -f /tegra114-microsoft-surface-2.dtb /mnt/target/boot/efi/tegra114-microsoft-surface-2.dtb
fi

cat << 'CMD_EOF' > /mnt/target/boot/efi/cmdline.txt
dtb=tegra114-microsoft-surface-2.dtb root=/dev/mmcblk0p2 console=tty1 consoleblank=0 loglevel=7 systemd.show_status=true cpuidle.off=1 rootwait rw
CMD_EOF

sync
umount /mnt/target/boot/efi
umount /mnt/target
sync

echo ""
echo "=========================================================================="
echo " [SUCCESS] Surface 2 Ubuntu 22.04 LTS Installation COMPLETE!             "
echo "=========================================================================="
echo " - Internal eMMC now contains the permanent Ubuntu 22.04 OS.              "
echo " - Dual 1080p Mirror (DSI + HDMI) and Touchscreen drivers are ready.      "
echo ""
echo " >>> STEP 1: UNPLUG / REMOVE THE USB DRIVE NOW <<<                        "
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
    temp_out = OUT_INITRD + ".tmp"
    with gzip.open(SRC_INITRD, 'rb') as f_in, gzip.open(temp_out, 'wb', compresslevel=1) as f_out:
        # Stream existing CPIO files except replaced ones and 'TRAILER!!!'
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

            if name in (
                'init',
                'surface-online.sh',
                'usr/local/bin/surface-online.sh',
                'surface2_smart_receiver.py',
                'wpa_supplicant.conf',
                'authorized_keys',
                'surface2-rootfs-golden.tar.gz',
                'surface2-boot-golden.tar.gz',
                'tegra114-microsoft-surface-2.dtb',
            ):
                # Skip replaced files
                f_in.read(filesize)
                pad2 = (4 - (filesize % 4)) % 4
                f_in.read(pad2)
                continue

            # Write existing entry
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

        # 1. Write updated /init installer script
        print("Injecting hardened /init installer script...")
        init_data = INIT_SCRIPT.encode('utf-8')
        f_out.write(make_header('init', len(init_data), mode=0o100755))
        f_out.write(init_data)
        f_out.write(pad4(len(init_data)))

        # 2. Add extra files
        extra_files = [
            ('surface-online.sh', ONLINE_SH),
            ('usr/local/bin/surface-online.sh', ONLINE_SH),
            ('surface2_smart_receiver.py', RECEIVER_PY),
            ('wpa_supplicant.conf', WIFI_CONF),
            ('authorized_keys', SSH_KEY),
            ('surface2-rootfs-golden.tar.gz', ROOTFS_TAR),
            ('surface2-boot-golden.tar.gz', BOOT_TAR),
            ('tegra114-microsoft-surface-2.dtb', DTB_FILE),
        ]

        for dest_name, src_path in extra_files:
            if not os.path.exists(src_path):
                print(f"Skipping missing optional file: {src_path}")
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
    print(f"[OK] Hardened standalone initrd generated: {OUT_INITRD} ({final_sz / (1024*1024):.1f} MB)")

if __name__ == '__main__':
    build()
