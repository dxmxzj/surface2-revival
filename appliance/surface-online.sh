#!/bin/bash
# Surface 2 Autonomous Online & Wi-Fi Management Daemon
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Direct all output to private log; protect credentials from /dev/tty1
mkdir -p /var/log /run /var/run /run/sshd /etc/dropbear /dev/pts
touch /var/log/surface-online.log
chmod 600 /var/log/surface-online.log
exec >> /var/log/surface-online.log 2>&1

exec 9>/run/surface-online.lock
flock -n 9 || exit 0

echo "=== Surface 2 Online Service Started: $(date) ==="

# Route external USB Type-A UTMI Pad 0 from XUSB_PADCTL (0x7009f000) to USB1 EHCI (0x7d000000)
python3 -c '
import mmap, os, struct
try:
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    # 1. CAR (0x60006000): deassert XUSB_PADCTL reset (W_CLR 0x43c bit 14),
    #    enable CLK_ENB_USB2_TRK (Y_SET 0x29c bit 18, div 0x6cc=6),
    #    assert XUSB_HOST/DEV (U_SET 0x310) & XUSB_SS (W_SET 0x438) resets,
    #    clear UTMIPLL IDDQ override (0x52c)
    car = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x60006000)
    car[0x43c:0x440] = struct.pack("<I", 0x00004000)
    car[0x6cc:0x6d0] = struct.pack("<I", 0x00000006)
    car[0x29c:0x2a0] = struct.pack("<I", 0x00040000)
    car[0x310:0x314] = struct.pack("<I", 0x82000000)
    car[0x438:0x43c] = struct.pack("<I", 0x10000000)
    pwrdn = struct.unpack("<I", car[0x52c:0x530])[0]
    car[0x52c:0x530] = struct.pack("<I", (pwrdn & ~0x2) | 0x1)
    car.close()
    # 2. XUSB_PADCTL (0x7009f000): Tegra 114 register offsets
    pad = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x7009f000)
    elpg = struct.unpack("<I", pad[0x01c:0x020])[0]
    pad[0x01c:0x020] = struct.pack("<I", (elpg & ~0x07000000) | 0x00770000)
    pad[0x004:0x008] = struct.pack("<I", 0x00000000)
    pad[0x00c:0x010] = struct.pack("<I", 0x000001ff)
    pad[0x010:0x014] = struct.pack("<I", 0x0000003f)
    for off in (0x090, 0x094):
        v = struct.unpack("<I", pad[off:off+4])[0]
        pad[off:off+4] = struct.pack("<I", v & ~0x00380000)
    for off in (0x098, 0x09c):
        v = struct.unpack("<I", pad[off:off+4])[0]
        pad[off:off+4] = struct.pack("<I", v & ~0x00000004)
    bias = struct.unpack("<I", pad[0x0a0:0x0a4])[0]
    pad[0x0a0:0x0a4] = struct.pack("<I", bias & ~0x00003000)
    pad.close()
    # 3. PMC (0x7000e400): clear Port 0 AO powerdown & sleepwalk, clear BIAS_MASTER_PROG_VAL
    pmc = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x7000e000)
    ao = struct.unpack("<I", pmc[0x4f0:0x4f4])[0]
    pmc[0x4f0:0x4f4] = struct.pack("<I", ao & ~0xf)
    slp = struct.unpack("<I", pmc[0x5fc:0x600])[0]
    pmc[0x5fc:0x600] = struct.pack("<I", (slp & ~0xff) | 0xc0)
    pmc[0x5ec:0x5f0] = struct.pack("<I", 0x00001001)
    bm = struct.unpack("<I", pmc[0x670:0x674])[0]
    pmc[0x670:0x674] = struct.pack("<I", bm & ~0x2)
    pmc.close()
    os.close(fd)
except Exception as e:
    pass
' 2>/dev/null || true

for d in /sys/bus/platform/devices/7d000000.usb* /sys/bus/platform/devices/ci_hdrc* /sys/bus/usb/devices/usb*; do
    [ -e "$d/power/control" ] && echo on > "$d/power/control" 2>/dev/null || true
done

# Load Marvell Avastar 88W8897 SDIO driver
KVER=$(uname -r)
insmod /lib/modules/$KVER/kernel/drivers/net/wireless/marvell/mwifiex/mwifiex.ko 2>/dev/null || true
insmod /lib/modules/$KVER/kernel/drivers/net/wireless/marvell/mwifiex/mwifiex_sdio.ko 2>/dev/null || true
modprobe mwifiex_sdio 2>/dev/null || true

ip link set lo up 2>/dev/null || true

# Function to locate wireless interface dynamically
find_wifi_iface() {
    for netdev in /sys/class/net/*; do
        devname=$(basename "$netdev")
        [ "$devname" = "lo" ] && continue
        if [ -d "$netdev/wireless" ] || [ -d "$netdev/phy80211" ] || echo "$devname" | grep -qE "^(mlan|wlan|wl)"; then
            echo "$devname"
            return 0
        fi
    done
    return 1
}

# Main supervision loop
LAST_HDMI_STATE=""
while true; do
    # 1. Keep internal display unblanked & apply HDMI PHY fix on hotplug/connected
    CUR_HDMI_STATE=$(cat /sys/class/drm/card0-HDMI-A-1/status 2>/dev/null || echo "unknown")
    if [ "$CUR_HDMI_STATE" != "$LAST_HDMI_STATE" ]; then
        echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null || true
        if [ "$CUR_HDMI_STATE" = "connected" ]; then
            /usr/local/bin/surface-hdmi-phy.py 2>/dev/null || true
        fi
        LAST_HDMI_STATE="$CUR_HDMI_STATE"
    fi

    # 2. Dynamic wireless interface discovery
    IFACE=$(find_wifi_iface)
    if [ -n "$IFACE" ]; then
        ip link set "$IFACE" up 2>/dev/null || true
        # Disable power save to prevent dropped connections without HDMI
        iw dev "$IFACE" set power_save off 2>/dev/null || true

        # 3. Check and start wpa_supplicant if not running
        if ! pgrep -f "wpa_supplicant.*$IFACE" >/dev/null 2>&1; then
            if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
                killall wpa_supplicant 2>/dev/null || true
                rm -rf /run/wpa_supplicant /var/run/wpa_supplicant
                wpa_supplicant -B -i "$IFACE" -c /etc/wpa_supplicant/wpa_supplicant.conf -D nl80211,wext >/dev/null 2>&1
                sleep 3
            fi
        fi

        # 4. Check IPv4 address; request DHCP if missing
        if ! ip -4 addr show "$IFACE" 2>/dev/null | grep -q "inet "; then
            killall dhclient 2>/dev/null || true
            dhclient -v "$IFACE" >/dev/null 2>&1 &
            sleep 4
        fi
    fi

    # 5. SSH daemon watchdog
    if ! pgrep -x sshd >/dev/null 2>&1 && ! pgrep -x dropbear >/dev/null 2>&1; then
        /usr/sbin/sshd 2>/dev/null || dropbear -R -E -p 22 2>/dev/null || true
    fi

    # 6. Optional heartbeat beacon (disabled unless SURFACE_BEACON_HOST is set)
    if [ -n "${SURFACE_BEACON_HOST:-}" ]; then
        IP=$(ip -4 addr show "${IFACE:-mlan0}" 2>/dev/null | grep -oE "inet [0-9.]+" | awk '{print $2}')
        if [ -n "$IP" ]; then
            ping -c 1 -W 1 "$SURFACE_BEACON_HOST" >/dev/null 2>&1 || true
            echo "SURFACE2_ONLINE IFACE=$IFACE IP=$IP ROOT=$(findmnt -n -o SOURCE / 2>/dev/null) $(uptime)" > "/dev/tcp/$SURFACE_BEACON_HOST/${SURFACE_BEACON_PORT:-9999}" 2>/dev/null || true
        fi
    fi

    sleep 5
done
