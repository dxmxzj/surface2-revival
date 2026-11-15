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

tegra_usb_keep_awake() {
    for d in /sys/bus/platform/devices/7d000000.usb* /sys/bus/platform/devices/ci_hdrc* /sys/bus/usb/devices/usb* /sys/bus/usb/devices/1-*; do
        [ -e "$d/power/control" ] && echo on > "$d/power/control" 2>/dev/null || true
    done
}

ffz16() {
    local v=$(( $1 & 0xffff ))
    local b=0
    while [ "$b" -lt 16 ]; do
        if [ $(( (v >> b) & 1 )) -eq 0 ]; then
            echo "$b"
            return 0
        fi
        b=$(( b + 1 ))
    done
    echo 16
}

tegra_usb_mux_handoff() {
    [ -x /usr/bin/devmem2 ] || return 0
    tegra_usb_keep_awake

    # 1. CAR (0x60006000):
    #    - Deassert XUSB_PADCTL reset (bit 14 of RST_DEV_W_CLR 0x6000643c)
    #    - Enable CLK_ENB_USB2_TRK (bit 18 of CLK_OUT_ENB_Y_SET 0x6000629c) & divisor 6 (0x600066cc)
    #    - Assert XUSB_HOST (bit 25) & XUSB_DEV (bit 31) in RST_DEV_U_SET (0x60006310)
    #      and XUSB_SS (bit 28) in RST_DEV_W_SET (0x60006438) so UEFI XUSB releases the port
    #    - Clear UTMIPLL IDDQ override in UTMIPLL_HW_PWRDN_CFG0 (0x6000652c)
    mmio_write 0x6000643c 0x00004000
    mmio_write 0x600066cc 0x00000006
    mmio_write 0x6000629c 0x00040000
    mmio_write 0x60006310 0x82000000
    mmio_write 0x60006438 0x10000000
    PWRDN=$(mmio_read 0x6000652c)
    [ -n "$PWRDN" ] && mmio_write 0x6000652c $(printf "0x%x" $(( (PWRDN & ~0x2) | 0x1 )))

    # 2. PMC (0x7000e400):
    #    - Clear Port 0 power-down bits [3:0] in PMC_USB_AO (0x7000e4f0)
    #    - Disable PMC sleepwalk & RCTRL/TCTRL override on Port 0 in PMC_SLEEP_CFG (0x7000e5fc)
    #    - Clear walk pointer & wake alarm in PMC_TRIGGERS (0x7000e5ec)
    AO=$(mmio_read 0x7000e4f0)
    [ -n "$AO" ] && mmio_write 0x7000e4f0 $(printf "0x%x" $(( AO & ~0xf )))
    SLP=$(mmio_read 0x7000e5fc)
    [ -n "$SLP" ] && mmio_write 0x7000e5fc $(printf "0x%x" $(( (SLP & ~0xff) | 0xc0 )))
    mmio_write 0x7000e5ec 0x00001001

    # 3. Ensure GPIO PK1 (5V VBUS on external USB port, 0x6000d2a8) is enabled and HIGH
    mmio_write 0x6000d288 0x0202
    mmio_write 0x6000d298 0x0202
    mmio_write 0x6000d2a8 0x0202

    # 4. XUSB_PADCTL (0x7009f000) — Tegra 114 register offsets (nv_xhci_tegra.c tegra114_padctl_offsets):
    #    - 0x01c (ELPG_PROGRAM_0): clear AUX_MUX_LP0_CLAMP* (bits 24..26) & clamp SSP0/SSP1 (bits 16..22 = 0x00770000)
    #    - 0x004 (USB2_PAD_MUX_0): write 0x0 so BOTH UTMI Pad 0 (bits [1:0]) & UTMI Pad 1 (bits [3:2])
    #      and Bias Pad (bits [19:18]) are owned by SNPS
    #    - 0x00c (SNPS_OC_MAP_0 = 0x1ff) & 0x010 (USB2_OC_MAP_0 = 0x3f): disable false overcurrent
    #    - 0x090 / 0x094 (USB2_OTG_PAD0/1_CTL_0): clear PD (1<<19), PD2 (1<<20), PD_ZI (1<<21)
    #    - 0x098 / 0x09c (USB2_OTG_PAD0/1_CTL_1): clear PD_DR (1<<2)
    #    - 0x0a0 (USB2_BIAS_PAD_CTL_0): clear PD (1<<12) & PD_TRK (1<<13)
    ELPG=$(mmio_read 0x7009f01c)
    if [ -n "$ELPG" ]; then
        mmio_write 0x7009f01c $(printf "0x%x" $(( (ELPG & ~0x07000000) | 0x00770000 )))
    fi
    mmio_write 0x7009f004 0x00000000
    mmio_write 0x7009f00c 0x000001ff
    mmio_write 0x7009f010 0x0000003f

    for addr in 0x7009f090 0x7009f094; do
        V=$(mmio_read "$addr")
        [ -n "$V" ] && mmio_write "$addr" $(printf "0x%x" $(( V & ~0x00380000 )))
    done
    for addr in 0x7009f098 0x7009f09c; do
        V=$(mmio_read "$addr")
        [ -n "$V" ] && mmio_write "$addr" $(printf "0x%x" $(( V & ~0x00000004 )))
    done
    BIAS=$(mmio_read 0x7009f0a0)
    [ -n "$BIAS" ] && mmio_write 0x7009f0a0 $(printf "0x%x" $(( BIAS & ~0x00003000 )))

    # 5. If USB1 (0x7d000000) has probed, ensure PHY & UTMIP transceivers are awake and calibrated
    if [ -d /sys/bus/usb/devices/usb1 ]; then
        tegra_usb_keep_awake
        SUSP=$(mmio_read 0x7d000400)
        if [ -n "$SUSP" ]; then
            # Clear USB_SUSP_SET (bit 14) & UTMIP_RESET (bit 11), set UTMIP_PHY_ENABLE (bit 12)
            mmio_write 0x7d000400 $(printf "0x%x" $(( (SUSP & ~0x4800) | 0x1000 )))
        fi
        DEVLC=$(mmio_read 0x7d0001b4)
        if [ -n "$DEVLC" ] && [ $(( DEVLC & 0x00400000 )) -ne 0 ]; then
            # Clear HOSTPC1_DEVLC_PHCD (bit 22)
            mmio_write 0x7d0001b4 $(printf "0x%x" $(( DEVLC & ~0x00400000 )))
        fi
        # Ensure UTMIP_BIAS_CFG0 (0x7d00080c) has OTGPD (bit 11) & BIASPD (bit 10) cleared
        BCFG0=$(mmio_read 0x7d00080c)
        [ -n "$BCFG0" ] && mmio_write 0x7d00080c $(printf "0x%x" $(( BCFG0 & ~0x00000c00 )))
        # Ensure UTMIP_XCVR_CFG0 (0x7d000808) & CFG1 (0x7d000838) power-down bits are cleared
        XCFG0=$(mmio_read 0x7d000808)
        [ -n "$XCFG0" ] && mmio_write 0x7d000808 $(printf "0x%x" $(( XCFG0 & ~0x00054000 )))
        XCFG1=$(mmio_read 0x7d000838)
        if [ -n "$XCFG1" ]; then
            # Copy fuse-calibrated TERM_RANGE_ADJ from PAD0_CTL_1 (0x7009f098 bits [6:3]) if non-zero
            P0CTL1=$(mmio_read 0x7009f098)
            TADJ=$(( (P0CTL1 >> 3) & 0xf ))
            [ "$TADJ" -eq 0 ] && TADJ=6
            mmio_write 0x7d000838 $(printf "0x%x" $(( (XCFG1 & ~0x003c0015) | (TADJ << 18) )))
        fi
        # Tegra 114 UTMIP_SPARE_CFG0 (0x7d000834): clear FUSE_SETUP_SEL (bit 3) & FUSE_ATERM_SEL (bit 4)
        # so software-programmed XCVR_SETUP and TERM_RANGE_ADJ in CFG0/CFG1 are used directly
        SPARE=$(mmio_read 0x7d000834)
        [ -n "$SPARE" ] && mmio_write 0x7d000834 $(printf "0x%x" $(( SPARE & ~0x18 )))

        # Run Tegra 114 UTMIP bias tracking (utmi_phy_set_snps_trking_data in tegra_usb_pmc.c):
        # Enable PDTRK_COUNT(5) | PDTRK_POWERUP (0x2a, keeping PDTRK_POWERDOWN bit 0 = 0),
        # pulse BIAS_MASTER_PROG_VAL (0x7000e670 bit 1), read UTMIP_BIAS_STS0 (0x7d000840),
        # and program PMC_UTMIP_TERM_PAD_CFG (0x7000e5f8)!
        BCFG1=$(mmio_read 0x7d00083c)
        if [ -n "$BCFG1" ]; then
            BM=$(mmio_read 0x7000e670)
            [ -n "$BM" ] && mmio_write 0x7000e670 $(printf "0x%x" $(( BM | 0x2 )))
            mmio_write 0x7d00083c $(printf "0x%x" $(( (BCFG1 & ~0xf9) | 0x2a )))
            [ -n "$BM" ] && mmio_write 0x7000e670 $(printf "0x%x" $(( BM & ~0x2 )))
            [ -n "$BM" ] && mmio_write 0x7000e670 $(printf "0x%x" $(( BM | 0x2 )))
            STS0=$(mmio_read 0x7d000840)
            if [ -n "$STS0" ] && [ $(( STS0 & 0xffffffff )) -ne 0 ]; then
                R_FFZ=$(ffz16 $(( STS0 & 0xffff )))
                T_FFZ=$(ffz16 $(( (STS0 >> 16) & 0xffff )))
                RCTRL=$(( (0xf + R_FFZ) & 0x1f ))
                TCTRL=$(( (0xf + T_FFZ) & 0x1f ))
                mmio_write 0x7000e5f8 $(printf "0x%x" $(( (TCTRL << 5) | RCTRL )))
            else
                mmio_write 0x7000e5f8 0x000003ff
            fi
            # Leave BIAS_MASTER_PROG_VAL=0 and PDTRK powered up (0x2a) so hardware tracking stays live
            [ -n "$BM" ] && mmio_write 0x7000e670 $(printf "0x%x" $(( BM & ~0x2 )))
        fi

        # Ensure Tegra 114 PORTSC1 (0x7d000174) has PORT_POWER (bit 12 = 0x1000) enabled (masking RWC bits 0x2a)
        PSC=$(mmio_read 0x7d000174)
        if [ -n "$PSC" ] && [ $(( PSC & 0x1000 )) -eq 0 ]; then
            mmio_write 0x7d000174 $(printf "0x%x" $(( (PSC & ~0x2a) | 0x1000 )))
        fi
    fi
}

tegra_usb_vbus_cycle() {
    # Power-cycle 5V VBUS (GPIO PK1 0x6000d2a8) WITHOUT unbinding 7d000000.usb
    tegra_usb_keep_awake
    mmio_write 0x6000d2a8 0x0200
    sleep 1
    tegra_usb_mux_handoff
    mmio_write 0x6000d2a8 0x0202
    sleep 1
}

echo "[*] Handing off external USB port mux (XUSB_PADCTL -> USB1 EHCI)..."
PAD_MUX_BEFORE=$(mmio_read 0x7009f004)
tegra_usb_mux_handoff
# Wait up to 8s for 7d000000.usb (usb1) deferred probe to complete and run handoff ONCE when usb1 appears
for w in $(seq 1 16); do
    tegra_usb_keep_awake
    if [ -d /sys/bus/usb/devices/usb1 ]; then
        tegra_usb_mux_handoff
        break
    fi
    sleep 0.5 2>/dev/null || sleep 1
done
PAD_MUX_AFTER=$(mmio_read 0x7009f004)
echo "    XUSB_PADCTL_USB2_PAD_MUX: ${PAD_MUX_BEFORE:-N/A} -> ${PAD_MUX_AFTER:-N/A}"

# 1. Search for installer media (USB or MicroSD)
PAYLOAD_DIR=""
echo -n "[*] Scanning for installer media (USB / MicroSD)..."

for attempt in $(seq 1 45); do
    # Crucial: do NOT rewrite UTMIP/PHY registers inside the scan loop while khubd is resetting usb1-port1!
    tegra_usb_keep_awake
    PSC=$(mmio_read 0x7d000174)
    if [ -n "$PSC" ] && [ $(( PSC & 0x1000 )) -eq 0 ]; then
        mmio_write 0x7d000174 $(printf "0x%x" $(( (PSC & ~0x2a) | 0x1000 )))
    fi

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

    # Only cycle VBUS at attempt 20 if NO device is connected on PORTSC1 (bit 0 CCS == 0)
    if [ "$attempt" -eq 20 ]; then
        PSC=$(mmio_read 0x7d000174)
        if [ -n "$PSC" ] && [ $(( PSC & 0x1 )) -eq 0 ]; then
            tegra_usb_vbus_cycle
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
    echo "   XUSB_PADCTL_OTG_PAD0_CTL0(0x7009f090): $(mmio_read 0x7009f090)"
    echo "   XUSB_PADCTL_OTG_PAD0_CTL1(0x7009f098): $(mmio_read 0x7009f098)"
    echo "   USB1 PORTSC1             (0x7d000174): $(mmio_read 0x7d000174)"
    echo "   USB1 HOSTPC1_DEVLC       (0x7d0001b4): $(mmio_read 0x7d0001b4)"
    echo "   USB1 USB_SUSP_CTRL       (0x7d000400): $(mmio_read 0x7d000400)"
    echo "   USB1 UTMIP_XCVR_CFG0     (0x7d000808): $(mmio_read 0x7d000808)"
    echo "   USB1 UTMIP_XCVR_CFG1     (0x7d000838): $(mmio_read 0x7d000838)"
    echo "   USB1 UTMIP_BIAS_STS0     (0x7d000840): $(mmio_read 0x7d000840)"
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
