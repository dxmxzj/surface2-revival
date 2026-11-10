#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mkdir -p /run /var/run /run/sshd /etc/dropbear /dev/pts

exec 9>/run/surface-online.lock
flock -n 9 || exit 0

KVER=$(uname -r)
insmod /lib/modules/$KVER/kernel/drivers/net/wireless/marvell/mwifiex/mwifiex.ko 2>/dev/null || true
insmod /lib/modules/$KVER/kernel/drivers/net/wireless/marvell/mwifiex/mwifiex_sdio.ko 2>/dev/null || true
modprobe mwifiex_sdio 2>/dev/null || true

IFACE=""
for i in $(seq 1 30); do
    for netdev in /sys/class/net/*; do
        devname=$(basename "$netdev")
        [ "$devname" = "lo" ] && continue
        if [ -d "$netdev/wireless" ] || [ -d "$netdev/phy80211" ] || echo "$devname" | grep -qE "^(mlan|wlan|wl)"; then
            IFACE="$devname"
            break
        fi
    done
    [ -n "$IFACE" ] && break
    sleep 1
done

ip link set lo up 2>/dev/null || true

while true; do
    /usr/local/bin/surface-hdmi-phy.py 2>/dev/null || true
    if [ -n "$IFACE" ]; then
        ip link set "$IFACE" up 2>/dev/null || true
        iw dev "$IFACE" set power_save off 2>/dev/null || true
        if ! pgrep -f "wpa_supplicant.*$IFACE" >/dev/null 2>&1; then
            killall wpa_supplicant 2>/dev/null || true
            rm -rf /run/wpa_supplicant /var/run/wpa_supplicant
            wpa_supplicant -B -i "$IFACE" -c /etc/wpa_supplicant/wpa_supplicant.conf -D nl80211,wext 2>/dev/null
            sleep 4
        fi
        if ! ip -4 addr show "$IFACE" | grep -q "inet "; then
            killall dhclient 2>/dev/null || true
            dhclient "$IFACE" >/dev/null 2>&1 &
            sleep 4
        fi
    fi
    if ! pgrep -x sshd >/dev/null 2>&1 && ! pgrep -x dropbear >/dev/null 2>&1; then
        /usr/sbin/sshd 2>/dev/null || dropbear -R -E -p 22 2>/dev/null
    fi
    IP=$(ip -4 addr show "${IFACE:-mlan0}" 2>/dev/null | grep -oE "inet [0-9.]+" | awk '{print $2}')
    if [ -n "$IP" ]; then
        ping -c 1 -W 1 192.168.86.49 >/dev/null 2>&1 || true
        echo "SURFACE2_ONLINE IFACE=$IFACE IP=$IP ROOT=$(findmnt -n -o SOURCE / 2>/dev/null) $(uptime)" > /dev/tcp/192.168.86.49/9999 2>/dev/null || true
    fi
    sleep 5
done
