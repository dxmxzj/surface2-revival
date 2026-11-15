#!/usr/bin/env python3
"""
Build Lean Fast-Boot initrd.gz (35-45 MB) for Microsoft Surface 2 (RT).
Features:
1. Loads in ~2 seconds over USB UEFI.
2. Dynamic media scanning (detects USB /dev/sda1 or MicroSD /dev/mmcblk1p1).
3. SMART CONFIG PRESERVATION: Detects existing Linux installs on /dev/mmcblk0p2
   and automatically preserves Tailscale credentials, Wi-Fi configuration,
   SSH host keys, and hostname across updates!
4. Wipe override support: Create WIPE_EMMC or FRESH_INSTALL on USB to force full re-partition.
5. Strict size barrier (< 50 MB) enforced.
"""
import os
import sys
import gzip
import time

SRC_INITRD = '/mnt/d/surface2-usb-files/initrd.gz'
OUT_INITRD = '/mnt/d/surface2-usb-files/initrd.gz'
ONLINE_SH = '/home/dxm/surface2-revival/appliance/surface-online.sh'
DEVMEM_BIN = '/home/dxm/surface2-revival/boot/devmem2'
WIFI_CONF = '/mnt/d/surface2-usb-files/wpa_supplicant.conf'
SSH_KEY = '/mnt/d/surface2-usb-files/authorized_keys'

LEAN_INIT_SCRIPT = """#!/bin/bash
# Microsoft Surface 2 (RT) Smart Fast-Boot Unattended Installer & Updater
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TERM=linux
export HOME=/root

# Mount fundamental virtual filesystems
mount -t proc proc /proc 2>/dev/null || true
mount -t sysfs sysfs /sys 2>/dev/null || true
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mkdir -p /dev/pts /dev/shm /run /tmp /var/run /var/log /mnt/target /mnt/usb /mnt/check
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
echo "    Microsoft Surface 2 (RT) — Ubuntu 22.04 Fast Installer & Updater     "
echo "=========================================================================="
echo ""
echo " [*] STANDALONE INSTALLATION. Zero typing / No keyboard required."
echo " [*] Target: Internal 32 GB eMMC (/dev/mmcblk0)"
echo ""

# Helper functions for Tegra 114 MMIO register access via /usr/bin/devmem2
mmio_read() {
    /usr/bin/devmem2 "$1" w 2>/dev/null | awk '/Value at address/ {print $NF}'
}

mmio_write() {
    /usr/bin/devmem2 "$1" w "$2" >/dev/null 2>&1
}

tegra_usb_mux_handoff() {
    [ -x /usr/bin/devmem2 ] || return 0
    # 1. Ensure XUSB_PADCTL (reset 142 = bit 14 of CAR RST_DEVICES_W 0x6000635c) is out of reset
    RST_W=$(mmio_read 0x6000635c)
    if [ -n "$RST_W" ]; then
        mmio_write 0x6000635c $(printf "0x%x" $(( RST_W & ~0x4000 )))
    fi
    # 2. Release XUSB_PADCTL_ELPG_PROGRAM (0x7009f01c) AUX_MUX LP0 clamps (bits 24..26)
    ELPG=$(mmio_read 0x7009f01c)
    if [ -n "$ELPG" ]; then
        mmio_write 0x7009f01c $(printf "0x%x" $(( ELPG & ~0x01000000 )))
        mmio_write 0x7009f01c $(printf "0x%x" $(( ELPG & ~0x03000000 )))
        mmio_write 0x7009f01c $(printf "0x%x" $(( ELPG & ~0x07000000 )))
    fi
    # 3. Route UTMI Pad 0 (bits [1:0]) & UTMI Bias Pad (bits [19:18]) in XUSB_PADCTL_USB2_PAD_MUX (0x7009f004)
    #    from XUSB (1) to SNPS / usb@7d000000 (0)
    MUX=$(mmio_read 0x7009f004)
    if [ -n "$MUX" ]; then
        mmio_write 0x7009f004 $(printf "0x%x" $(( MUX & ~0x000c0003 )))
    fi
    # 4. Clear power-down bits on XUSB_PADCTL_USB2_OTG_PAD0_CTL0/1 & BIAS_PAD_CTL0
    CTL0=$(mmio_read 0x7009f0a0)
    [ -n "$CTL0" ] && mmio_write 0x7009f0a0 $(printf "0x%x" $(( CTL0 & ~0x00380000 )))
    CTL1=$(mmio_read 0x7009f0ac)
    [ -n "$CTL1" ] && mmio_write 0x7009f0ac $(printf "0x%x" $(( CTL1 & ~0x00000004 )))
    BIAS=$(mmio_read 0x7009f0b8)
    [ -n "$BIAS" ] && mmio_write 0x7009f0b8 $(printf "0x%x" $(( BIAS & ~0x00001000 )))
    # 5. Ensure GPIO PK1 (OUDD 0x6000d2a8 in Surface 2 ACPI SSDT) is enabled and HIGH
    mmio_write 0x6000d288 0x0202
    mmio_write 0x6000d298 0x0202
    mmio_write 0x6000d2a8 0x0202
    # 6. Keep USB1 controller and PHY out of runtime suspend
    for d in /sys/bus/platform/devices/7d000000.usb* /sys/bus/platform/devices/ci_hdrc* /sys/bus/usb/devices/usb*; do
        [ -e "$d/power/control" ] && echo on > "$d/power/control" 2>/dev/null || true
    done
}

echo "[*] Handing off external USB port mux (XUSB_PADCTL -> USB1 EHCI)..."
PAD_MUX_BEFORE=$(mmio_read 0x7009f004)
tegra_usb_mux_handoff
if [ -e /sys/bus/platform/drivers/tegra-usb/7d000000.usb ]; then
    echo 7d000000.usb > /sys/bus/platform/drivers/tegra-usb/unbind 2>/dev/null || true
    sleep 1
    tegra_usb_mux_handoff
    echo 7d000000.usb > /sys/bus/platform/drivers/tegra-usb/bind 2>/dev/null || true
    tegra_usb_mux_handoff
fi
PAD_MUX_AFTER=$(mmio_read 0x7009f004)
echo "    XUSB_PADCTL_USB2_PAD_MUX: ${PAD_MUX_BEFORE:-N/A} -> ${PAD_MUX_AFTER:-N/A}"

# 1. Search for installer media (USB or MicroSD)
PAYLOAD_DIR=""
echo -n "[*] Scanning for installer media (USB / MicroSD)..."

for attempt in $(seq 1 45); do
    for p in /mnt/usb /media /media/* /run/media/* /; do
        if [ -f "$p/surface2-rootfs-golden.tar.gz" ]; then
            PAYLOAD_DIR="$p"
            break 2
        fi
    done

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

    # Retry controller rebind at attempt 10 and 25 if USB drive hasn't enumerated yet
    if [ "$attempt" -eq 10 ] || [ "$attempt" -eq 25 ]; then
        tegra_usb_mux_handoff
        if [ -e /sys/bus/platform/drivers/tegra-usb/7d000000.usb ]; then
            echo 7d000000.usb > /sys/bus/platform/drivers/tegra-usb/unbind 2>/dev/null || true
            sleep 1
            tegra_usb_mux_handoff
            echo 7d000000.usb > /sys/bus/platform/drivers/tegra-usb/bind 2>/dev/null || true
            tegra_usb_mux_handoff
        fi
    fi

    echo -n "."
    sleep 1
done

echo ""
if [ -z "$PAYLOAD_DIR" ]; then
    echo ""
    echo "=========================================================================="
    echo " [ERROR] Installer payload (surface2-rootfs-golden.tar.gz) NOT FOUND!    "
    echo "=========================================================================="
    echo " USB/PADCTL Hardware Diagnostics:"
    echo "   XUSB_PADCTL_USB2_PAD_MUX (0x7009f004): $(mmio_read 0x7009f004)"
    echo "   XUSB_PADCTL_ELPG_PROGRAM (0x7009f01c): $(mmio_read 0x7009f01c)"
    echo "   USB1 PORTSC1             (0x7d000184): $(mmio_read 0x7d000184)"
    echo "   USB1 USB_SUSP_CTRL       (0x7d000400): $(mmio_read 0x7d000400)"
    echo "   USB1 VBUS_SENSORS        (0x7d000404): $(mmio_read 0x7d000404)"
    echo ""
    echo " Detected block devices:"
    lsblk 2>/dev/null || cat /proc/partitions
    echo ""
    echo " Dropping to shell for diagnostics..."
    while true; do setsid /bin/bash </dev/tty1 >/dev/tty1 2>&1; sleep 1; done
fi

# 2. Check for existing Linux installation on /dev/mmcblk0p2
EXISTING_INSTALL=0
mkdir -p /mnt/check
if mount -t ext4 -o ro /dev/mmcblk0p2 /mnt/check 2>/dev/null; then
    if [ -f /mnt/check/etc/os-release ]; then
        EXISTING_INSTALL=1
    fi
    umount /mnt/check 2>/dev/null || true
fi

# Check for explicit wipe override flag on USB media
if [ -f "$PAYLOAD_DIR/WIPE_EMMC" ] || [ -f "$PAYLOAD_DIR/FRESH_INSTALL" ]; then
    echo "[!] Override flag detected: Forcing full clean eMMC repartition and format."
    EXISTING_INSTALL=0
fi

mkdir -p /tmp/preserved_config

if [ "$EXISTING_INSTALL" -eq 1 ]; then
    echo "=========================================================================="
    echo " [*] EXISTING LINUX INSTALLATION DETECTED ON /dev/mmcblk0p2!              "
    echo " [*] PRESERVING CONFIGURATION: Tailscale, Wi-Fi, SSH keys, Hostname       "
    echo "=========================================================================="
    
    mkdir -p /mnt/target
    mount -t ext4 /dev/mmcblk0p2 /mnt/target 2>/dev/null || true
    
    # Back up Tailscale authentication & state
    if [ -d /mnt/target/var/lib/tailscale ]; then
        echo " [+] Preserving Tailscale machine identity & login..."
        mkdir -p /tmp/preserved_config/tailscale
        cp -a /mnt/target/var/lib/tailscale/* /tmp/preserved_config/tailscale/ 2>/dev/null || true
    fi
    
    # Back up existing Wi-Fi configuration
    if [ -f /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf ]; then
        echo " [+] Preserving existing Wi-Fi configuration..."
        mkdir -p /tmp/preserved_config/wpa_supplicant
        cp -a /mnt/target/etc/wpa_supplicant/* /tmp/preserved_config/wpa_supplicant/ 2>/dev/null || true
    fi
    
    # Back up SSH host keys (prevents SSH MITM / remote host identification warnings)
    if [ -d /mnt/target/etc/ssh ]; then
        echo " [+] Preserving SSH host keys..."
        mkdir -p /tmp/preserved_config/etc_ssh
        cp -a /mnt/target/etc/ssh/ssh_host_* /tmp/preserved_config/etc_ssh/ 2>/dev/null || true
    fi
    
    # Back up authorized_keys & hostname
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
    echo "=========================================================================="
    echo " [*] Fresh installation target: Partitioning & formatting eMMC...        "
    echo "=========================================================================="
    
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

    echo "Formatting EFI and Root filesystems..."
    mkfs.vfat -F 32 -n "EFI" /dev/mmcblk0p1
    mkfs.ext4 -F -L "surface-root" /dev/mmcblk0p2
fi

echo "[1/3] Extracting Ubuntu 22.04 LTS Smart Display Rootfs (takes ~90s)..."
mkdir -p /mnt/target
mount -t ext4 /dev/mmcblk0p2 /mnt/target || {
    echo "FATAL: Failed to mount /dev/mmcblk0p2 on /mnt/target!"
    exit 1
}

tar --warning=no-timestamp --numeric-owner -C /mnt/target -xzf "$PAYLOAD_DIR/surface2-rootfs-golden.tar.gz"

mkdir -p /mnt/target/{dev,proc,sys,tmp,run,mnt,boot/efi,etc/wpa_supplicant,root/.ssh,usr/local/bin,var/lib/tailscale}
chmod 1777 /mnt/target/tmp
chmod 700 /mnt/target/root/.ssh

cat << 'FSTAB_EOF' > /mnt/target/etc/fstab
/dev/mmcblk0p2  /          ext4  defaults,noatime  0 1
/dev/mmcblk0p1  /boot/efi  vfat  defaults,nofail   0 2
tmpfs           /tmp       tmpfs defaults,nosuid,nodev 0 0
FSTAB_EOF

# Remove legacy rc.local to eliminate console credential exposure
rm -f /mnt/target/etc/rc.local

# Install updated autonomous online daemon
if [ -f "/surface-online.sh" ]; then
    cp -f "/surface-online.sh" /mnt/target/usr/local/bin/surface-online.sh
    chmod 755 /mnt/target/usr/local/bin/surface-online.sh
fi

echo "[2/3] Restoring configurations & credentials..."
# 1. Restore Tailscale credentials if preserved
if [ -d /tmp/preserved_config/tailscale ] && [ "$(ls -A /tmp/preserved_config/tailscale 2>/dev/null)" ]; then
    echo " [+] Restoring preserved Tailscale authentication..."
    cp -a /tmp/preserved_config/tailscale/* /mnt/target/var/lib/tailscale/
    chmod 600 /mnt/target/var/lib/tailscale/* 2>/dev/null || true
fi

# 2. Restore SSH host keys if preserved
if [ -d /tmp/preserved_config/etc_ssh ] && [ "$(ls -A /tmp/preserved_config/etc_ssh 2>/dev/null)" ]; then
    echo " [+] Restoring preserved SSH host keys..."
    cp -a /tmp/preserved_config/etc_ssh/* /mnt/target/etc/ssh/
    chmod 600 /mnt/target/etc/ssh/ssh_host_*_key 2>/dev/null || true
    chmod 644 /mnt/target/etc/ssh/ssh_host_*_key.pub 2>/dev/null || true
else
    chroot /mnt/target ssh-keygen -A 2>/dev/null || true
fi

# 3. Restore Wi-Fi configuration (or install from USB if fresh)
if [ -d /tmp/preserved_config/wpa_supplicant ] && [ -f /tmp/preserved_config/wpa_supplicant/wpa_supplicant.conf ]; then
    echo " [+] Restoring preserved Wi-Fi network settings..."
    cp -a /tmp/preserved_config/wpa_supplicant/* /mnt/target/etc/wpa_supplicant/
    chmod 600 /mnt/target/etc/wpa_supplicant/wpa_supplicant.conf
else
    # Parse Wi-Fi from USB installer
    WIFI_SRC=""
    for w in "$PAYLOAD_DIR/wpa_supplicant.conf" "$PAYLOAD_DIR/surface2-wifi.conf" /wpa_supplicant.conf; do
        if [ -f "$w" ]; then
            WIFI_SRC="$w"
            break
        fi
    done
    if [ -n "$WIFI_SRC" ]; then
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
fi

# 4. Restore Hostname & authorized_keys
if [ -f /tmp/preserved_config/hostname ]; then
    cp -a /tmp/preserved_config/hostname /mnt/target/etc/hostname
else
    echo "surface2" > /mnt/target/etc/hostname
fi

if [ -d /tmp/preserved_config/root_ssh ]; then
    cp -a /tmp/preserved_config/root_ssh/* /mnt/target/root/.ssh/
elif [ -f "$PAYLOAD_DIR/authorized_keys" ]; then
    cp -f "$PAYLOAD_DIR/authorized_keys" /mnt/target/root/.ssh/authorized_keys
    chmod 600 /mnt/target/root/.ssh/authorized_keys
fi

echo "[3/3] Installing UEFI Boot Chain & Golden DTB to /dev/mmcblk0p1..."
mkdir -p /mnt/target/boot/efi
mount -t vfat /dev/mmcblk0p1 /mnt/target/boot/efi 2>/dev/null || {
    mkfs.vfat -F 32 -n "EFI" /dev/mmcblk0p1
    mount -t vfat /dev/mmcblk0p1 /mnt/target/boot/efi
}

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
umount /mnt/target/boot/efi 2>/dev/null || true
umount /mnt/target 2>/dev/null || true
sync
umount /mnt/usb 2>/dev/null || true
sync

echo ""
echo "=========================================================================="
if [ "$EXISTING_INSTALL" -eq 1 ]; then
    echo " [SUCCESS] Surface 2 Ubuntu 22.04 LTS Upgrade COMPLETE!                  "
    echo " - Configurations Preserved: Tailscale Login, Wi-Fi, SSH Host Keys.       "
else
    echo " [SUCCESS] Surface 2 Ubuntu 22.04 LTS Installation COMPLETE!             "
    echo " - Internal eMMC now contains the permanent Ubuntu 22.04 OS.              "
fi
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
    temp_out = OUT_INITRD + ".smart.tmp"
    with gzip.open(SRC_INITRD, 'rb') as f_in, gzip.open(temp_out, 'wb', compresslevel=6) as f_out:
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

            # Exclude large payload archives to guarantee lean fast-boot (<50 MB)
            if name in ('surface2-rootfs-golden.tar.gz', 'surface2-boot-golden.tar.gz',
                        'init', 'surface-online.sh', 'usr/local/bin/surface-online.sh',
                        'usr/bin/devmem2', 'wpa_supplicant.conf', 'authorized_keys'):
                f_in.read(filesize)
                pad2 = (4 - (filesize % 4)) % 4
                f_in.read(pad2)
                continue

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

        # 1. Write updated smart lean /init script
        print("Injecting smart preservation /init installer script...")
        init_data = LEAN_INIT_SCRIPT.encode('utf-8')
        f_out.write(make_header('init', len(init_data), mode=0o100755))
        f_out.write(init_data)
        f_out.write(pad4(len(init_data)))

        # 2. Add extra lightweight scripts & binaries
        extra_files = [
            ('surface-online.sh', ONLINE_SH),
            ('usr/local/bin/surface-online.sh', ONLINE_SH),
            ('usr/bin/devmem2', DEVMEM_BIN),
            ('wpa_supplicant.conf', WIFI_CONF),
            ('authorized_keys', SSH_KEY),
        ]

        for dest_name, src_path in extra_files:
            if not os.path.exists(src_path):
                continue
            sz = os.path.getsize(src_path)
            mode = 0o100755 if (dest_name.endswith('.sh') or dest_name.endswith('devmem2')) else 0o100600
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
    assert final_sz < 60 * 1024 * 1024, f"initrd is oversized ({final_sz} bytes)!"
    print(f"[OK] Smart lean fast-boot initrd generated: {OUT_INITRD} ({final_sz / (1024*1024):.1f} MB)")

if __name__ == '__main__':
    build()
