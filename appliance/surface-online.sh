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
    car = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x60006000)
    rst_w = struct.unpack("<I", car[0x35c:0x360])[0]
    car[0x35c:0x360] = struct.pack("<I", rst_w & ~0x4000)
    car.close()
    pad = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x7009f000)
    elpg = struct.unpack("<I", pad[0x01c:0x020])[0]
    pad[0x01c:0x020] = struct.pack("<I", elpg & ~0x07000000)
    mux = struct.unpack("<I", pad[0x004:0x008])[0]
    pad[0x004:0x008] = struct.pack("<I", mux & ~0x000c0003)
    ctl0 = struct.unpack("<I", pad[0x0a0:0x0a4])[0]
    pad[0x0a0:0x0a4] = struct.pack("<I", ctl0 & ~0x00380000)
    ctl1 = struct.unpack("<I", pad[0x0ac:0x0b0])[0]
    pad[0x0ac:0x0b0] = struct.pack("<I", ctl1 & ~0x00000004)
    bias = struct.unpack("<I", pad[0x0b8:0x0bc])[0]
    pad[0x0b8:0x0bc] = struct.pack("<I", bias & ~0x00001000)
    pad.close()
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
while true; do
    # 1. Apply HDMI PHY fix if HDMI is connected (safe no-op if disconnected)
    /usr/local/bin/surface-hdmi-phy.py 2>/dev/null || true

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

    # 6. Periodic heartbeat beacon
    IP=$(ip -4 addr show "${IFACE:-mlan0}" 2>/dev/null | grep -oE "inet [0-9.]+" | awk '{print $2}')
    if [ -n "$IP" ]; then
        ping -c 1 -W 1 192.168.86.49 >/dev/null 2>&1 || true
        echo "SURFACE2_ONLINE IFACE=$IFACE IP=$IP ROOT=$(findmnt -n -o SOURCE / 2>/dev/null) $(uptime)" > /dev/tcp/192.168.86.49/9999 2>/dev/null || true
    fi

    sleep 5
done
