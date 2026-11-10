#!/usr/bin/env python3
"""
Smart Screen Mirror & Dashboard Receiver for Microsoft Surface 2 (Tegra 4)
Features:
- Dual-Display 1080p Smart Display Dashboard via /dev/fb0 (mirrors simultaneously on internal 1080p DSI panel & Micro-HDMI)
- Automatic Tegra 114 HDMI SOR PHY High-Drive Calibration for rock-solid 1080p60 output to GLKVM / external monitors
- RTSP / HTTP Video Stream Receiver via MPV / FFmpeg (with Tegra VDE /dev/video0 hardware decode)
- Apple AirPlay Auto-Switching (UxPlay on port 7000)
- Built-in Web Controller & REST API on port 8080 (/play, /stop, /status, /terminal, /screenshot)
- Instant Drop-to-Terminal (tty1) via Keyboard ('q' or 'Esc'), Touchscreen button tap, CLI ('dashboard stop'), or Web UI
"""

import os
import sys
import glob
import mmap
import time
import json
import fcntl
import struct
import socket
import signal
import datetime
import threading
import subprocess
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PIL import Image, ImageDraw, ImageFont

MPV_SOCKET = "/tmp/mpv.sock"
DASHBOARD_IMG = "/tmp/dashboard.png"
HTTP_PORT = 8080
AIRPLAY_PORT = 7000


def apply_hdmi_phy_fix():
    """
    Program NVIDIA Tegra 114 high-speed (>148.5 MHz) TMDS PHY drive & peak current
    registers on hdmi@54280000 so 1920x1080@60 over Micro-HDMI locks cleanly without
    dropouts or white flashes. Safe when HDMI is unplugged (verifies CAR clock & reset).
    """
    try:
        st_path = "/sys/class/drm/card0-HDMI-A-1/status"
        en_path = "/sys/class/drm/card0-HDMI-A-1/enabled"
        if os.path.exists(st_path) and open(st_path).read().strip() != "connected":
            return
        if os.path.exists(en_path) and open(en_path).read().strip() != "enabled":
            return
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            # Verify HDMI clock (bit 19 in CLK_OUT_ENB_H @ 0x014) is ON and reset (RST_DEVICES_H @ 0x008) is OFF
            car = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x60006000)
            try:
                rst_h = struct.unpack("<I", car[0x008:0x00C])[0]
                clk_h = struct.unpack("<I", car[0x014:0x018])[0]
                if ((clk_h >> 19) & 1) == 0 or ((rst_h >> 19) & 1) != 0:
                    return
            finally:
                car.close()

            m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x54280000)
            try:
                def w(reg, val):
                    m[reg * 4 : reg * 4 + 4] = struct.pack("<I", val)
                w(0x57, 0x01003F10)  # HDMI_NV_PDISP_SOR_PLL0
                w(0x58, 0x00300F00)  # HDMI_NV_PDISP_SOR_PLL1
                w(0x99, 0x00000000)  # HDMI_NV_PDISP_PE_CURRENT
                w(0x7E, 0x303F3F3F)  # HDMI_NV_PDISP_SOR_LANE_DRIVE_CURRENT (0x30 clk, 0x3f data)
                w(0xD1, 0x040F0F0F)  # HDMI_NV_PDISP_SOR_IO_PEAK_CURRENT
            finally:
                m.close()
        finally:
            os.close(fd)
    except Exception:
        pass


def set_fbcon_cursor(enabled: bool):
    try:
        with open("/sys/class/graphics/fbcon/cursor_blink", "w") as f:
            f.write("1\n" if enabled else "0\n")
    except Exception:
        pass
    try:
        with open("/dev/tty1", "w") as tty:
            tty.write("\033[?25h" if enabled else "\033[?25l")
            tty.flush()
    except Exception:
        pass


def get_lan_ip():
    for iface in ("mlan0", "wlan0", "eth0"):
        out = subprocess.getoutput(f"ip -4 -o addr show dev {iface} 2>/dev/null").strip()
        if out:
            for part in out.split():
                if "/" in part and part[0].isdigit():
                    return part.split("/")[0]
    out = subprocess.getoutput("hostname -I 2>/dev/null").strip()
    return out.split()[0] if out else "Disconnected"


def get_tailscale_ip():
    out = subprocess.getoutput("tailscale ip -4 2>/dev/null").strip()
    if out and out[0].isdigit():
        return out.splitlines()[0]
    return "Not configured"


def get_battery_status():
    for bat in glob.glob("/sys/class/power_supply/*"):
        cap_file = os.path.join(bat, "capacity")
        st_file = os.path.join(bat, "status")
        if os.path.exists(cap_file):
            try:
                cap = open(cap_file).read().strip()
                st = open(st_file).read().strip() if os.path.exists(st_file) else ""
                return f"{cap}% ({st})" if st else f"{cap}%"
            except Exception:
                pass
    return "AC / External Power"


def restore_tty1_terminal(reason="User requested terminal"):
    """Restore tty1 text console, print helpful shortcut banner, and start Touch Keyboard."""
    set_fbcon_cursor(True)
    # Restrict tty1 to top 38 rows so the On-Screen Touch Keyboard (y=630..1080) never overlaps text
    try:
        fd = os.open("/dev/tty1", os.O_RDWR)
        try:
            fcntl.ioctl(fd, 0x5414, struct.pack("HHHH", 38, 240, 1920, 38 * 16))
        finally:
            os.close(fd)
    except Exception:
        pass
    banner = (
        "\033[2J\033[H"
        "\033[1;36m========================================================================\033[0m\r\n"
        "  \033[1;32mMicrosoft Surface 2 Smart Display Paused — Interactive Terminal (tty1)\033[0m\r\n"
        f"  Reason : {reason}\r\n"
        "  • Type \033[1;33mdashboard\033[0m or tap \033[1;32m[ Launch Dashboard ]\033[0m below to relaunch\r\n"
        "  • Type \033[1;33mdashboard kbd-off\033[0m or tap \033[1;33m[ Hide Keyboard ]\033[0m for full-screen shell\r\n"
        "  • Type \033[1;33mcast play <url>\033[0m to play an RTSP/HTTP video stream\r\n"
        "\033[1;36m========================================================================\033[0m\r\n"
    )
    try:
        subprocess.run(["chvt", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        with open("/dev/tty1", "w") as tty:
            tty.write(banner)
            tty.flush()
        # Inject Enter (\n) via TIOCSTI (0x5412) so the shell prompt redraws immediately
        fd = os.open("/dev/tty1", os.O_RDWR)
        try:
            fcntl.ioctl(fd, 0x5412, b"\n")
        finally:
            os.close(fd)
    except Exception:
        pass
    subprocess.Popen("systemctl start surface-touch-kbd.service 2>/dev/null", shell=True)


def drop_to_terminal(reason="User requested terminal"):
    """Stop the dashboard kiosk and return cleanly to tty1 with On-Screen Touch Keyboard."""
    print(f"[SmartReceiver] Dropping to terminal (tty1): {reason}", flush=True)
    restore_tty1_terminal(reason)
    subprocess.Popen(
        "systemctl stop surface-dashboard.service 2>/dev/null; killall cage mpv 2>/dev/null",
        shell=True
    )
    os._exit(0)


class MPVClient:
    def __init__(self, sock_path=MPV_SOCKET):
        self.sock_path = sock_path

    def send_command(self, cmd_list, timeout=2.0):
        if not os.path.exists(self.sock_path):
            return None
        s = None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(self.sock_path)
            req = json.dumps({"command": cmd_list}) + "\n"
            s.sendall(req.encode("utf-8"))
            resp = s.recv(4096)
            return json.loads(resp.decode("utf-8", errors="ignore"))
        except Exception:
            return None
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    def load_file(self, path_or_url):
        return self.send_command(["loadfile", path_or_url, "replace"])

    def stop(self):
        return self.send_command(["stop"])


class DashboardGenerator:
    def __init__(self, output_path=DASHBOARD_IMG):
        self.output_path = output_path
        try:
            self.font_xl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 105)
            self.font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
            self.font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 25)
            self.font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            self.font_btn = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except Exception:
            self.font_xl = self.font_lg = self.font_md = self.font_sm = self.font_btn = ImageFont.load_default()

    def render_to_fb0(self, img: Image.Image):
        """Write 1920x1080 RGB PIL Image directly to /dev/fb0 (32bpp BGRA) for dual DSI+HDMI display."""
        try:
            r, g, b = img.split()
            a = Image.new("L", img.size, 255)
            bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
            with open("/dev/fb0", "r+b") as fb:
                fb.seek(0)
                fb.write(bgra)
        except Exception as e:
            print(f"[DashboardGenerator] fb0 write error: {e}", flush=True)

    def generate(self, status_msg="Ready to receive streams", write_fb0=True):
        img = Image.new("RGB", (1920, 1080), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        now = datetime.datetime.now()
        time_str = now.strftime("%H:%M:%S")
        date_str = now.strftime("%A, %B %d, %Y")

        # Header
        draw.text((100, 65), "MICROSOFT SURFACE 2 (TEGRA 4)", font=self.font_sm, fill=(148, 163, 184))
        draw.text((100, 95), "Smart Screen Mirror & Video Appliance", font=self.font_md, fill=(226, 232, 240))

        # Top-Right Touch/Keyboard Exit-to-Terminal Button (x: 1380..1820, y: 55..135)
        draw.rounded_rectangle([(1380, 55), (1820, 135)], radius=14, fill=(30, 41, 59), outline=(245, 158, 11), width=2)
        draw.text((1405, 70), "[ Terminal / Shell (tty1) ]", font=self.font_btn, fill=(251, 191, 36))
        draw.text((1405, 100), "Tap here or press 'Q' / 'Esc'", font=self.font_sm, fill=(203, 213, 225))

        # Clock & Date
        draw.text((100, 190), time_str, font=self.font_xl, fill=(248, 250, 252))
        draw.text((105, 320), date_str, font=self.font_lg, fill=(56, 189, 248))

        # Status badge
        draw.rounded_rectangle([(100, 390), (900, 445)], radius=10, fill=(30, 41, 59))
        draw.text((120, 405), "● " + status_msg, font=self.font_sm, fill=(52, 211, 153))

        # System info card
        draw.rounded_rectangle([(100, 480), (900, 980)], radius=20, fill=(30, 41, 59), outline=(51, 65, 85), width=2)
        draw.text((130, 510), "SYSTEM STATUS", font=self.font_sm, fill=(148, 163, 184))

        lan_ip = get_lan_ip()
        ts_ip = get_tailscale_ip()
        hostname = socket.gethostname()
        battery = get_battery_status()
        load = os.getloadavg()[0]

        draw.text((130, 560), f"Device:         {hostname} (Internal 32GB eMMC)", font=self.font_md, fill=(241, 245, 249))
        draw.text((130, 610), f"Wi-Fi IPv4:     {lan_ip} (mlan0)", font=self.font_md, fill=(52, 211, 153))
        draw.text((130, 660), f"Tailscale IP:   {ts_ip}", font=self.font_md, fill=(56, 189, 248))
        draw.text((130, 710), f"CPU Load:       {load:.2f} (Quad-Core Cortex-A15)", font=self.font_md, fill=(241, 245, 249))
        draw.text((130, 760), f"Battery:        {battery}", font=self.font_md, fill=(251, 191, 36))
        draw.text((130, 810), "Acceleration:   Tegra VDE H.264 (/dev/video0)", font=self.font_md, fill=(56, 189, 248))
        draw.text((130, 860), "Displays:       1080p Internal LCD + 1080p60 HDMI", font=self.font_md, fill=(241, 245, 249))

        # How to stream & terminal shortcuts card
        draw.rounded_rectangle([(960, 480), (1820, 980)], radius=20, fill=(30, 41, 59), outline=(51, 65, 85), width=2)
        draw.text((990, 510), "HOW TO STREAM & TERMINAL CONTROLS", font=self.font_sm, fill=(148, 163, 184))

        draw.text((990, 560), "1. Apple AirPlay Mirroring (Mac / iPhone / iPad):", font=self.font_md, fill=(241, 245, 249))
        draw.text((1020, 600), "• Open Control Center -> Screen Mirroring", font=self.font_sm, fill=(203, 213, 225))
        draw.text((1020, 630), "• Select 'Surface2' to mirror screen & audio", font=self.font_sm, fill=(56, 189, 248))

        draw.text((990, 685), "2. RTSP / Video Stream Casting (OBS / VLC / Web):", font=self.font_md, fill=(241, 245, 249))
        draw.text((1020, 725), f"• Web Controller: http://{lan_ip}:{HTTP_PORT}", font=self.font_sm, fill=(56, 189, 248))
        draw.text((1020, 755), f"• HTTP Trigger:   curl 'http://{lan_ip}:{HTTP_PORT}/play?url=rtsp://...'", font=self.font_sm, fill=(203, 213, 225))
        draw.text((1020, 785), "• CLI Shortcut:   cast play <rtsp-stream-url>", font=self.font_sm, fill=(203, 213, 225))

        draw.text((990, 840), "3. Terminal & Dashboard Switching:", font=self.font_md, fill=(251, 191, 36))
        draw.text((1020, 880), "• Drop to Terminal: Press 'Q' / 'Esc' or Tap Top-Right Button", font=self.font_sm, fill=(203, 213, 225))
        draw.text((1020, 910), "• Relaunch Dashboard from Terminal: Type 'dashboard'", font=self.font_sm, fill=(52, 211, 153))

        tmp_out = self.output_path + ".tmp.png"
        img.save(tmp_out)
        os.replace(tmp_out, self.output_path)
        if write_fb0:
            self.render_to_fb0(img)


class SmartReceiverServer:
    def __init__(self):
        self.mpv_client = MPVClient(MPV_SOCKET)
        self.dashboard_gen = DashboardGenerator(DASHBOARD_IMG)
        self.state = "IDLE"  # "IDLE", "STREAMING", "AIRPLAY"
        self.current_url = ""
        self.state_lock = threading.Lock()
        self.mpv_proc = None
        self.intentional_mpv_stop = False

    def stop_mpv_if_running(self):
        self.intentional_mpv_stop = True
        if self.mpv_proc and self.mpv_proc.poll() is None:
            try:
                self.mpv_proc.terminate()
                self.mpv_proc.wait(timeout=2)
            except Exception:
                try:
                    self.mpv_proc.kill()
                except Exception:
                    pass
        self.mpv_proc = None

    def start_mpv(self, initial_target=None):
        self.stop_mpv_if_running()
        if os.path.exists(MPV_SOCKET):
            try:
                os.remove(MPV_SOCKET)
            except Exception:
                pass

        self.intentional_mpv_stop = False
        target = initial_target or DASHBOARD_IMG

        cmd = [
            "mpv",
            "--idle=yes",
            f"--input-ipc-server={MPV_SOCKET}",
            "--hwdec=v4l2m2m-copy",
            "--vo=drm",
            "--fullscreen",
            "--no-osd-bar",
            "--keep-open=yes",
            target
        ]
        self.mpv_proc = subprocess.Popen(cmd)
        for _ in range(50):
            if os.path.exists(MPV_SOCKET):
                break
            time.sleep(0.1)
        apply_hdmi_phy_fix()
        return self.mpv_proc

    def show_dashboard(self, status="Ready to receive streams"):
        with self.state_lock:
            self.state = "IDLE"
            self.current_url = ""
        self.stop_mpv_if_running()
        set_fbcon_cursor(False)
        apply_hdmi_phy_fix()
        self.dashboard_gen.generate(status, write_fb0=True)

    def play_stream(self, url):
        with self.state_lock:
            self.state = "STREAMING"
            self.current_url = url
        if self.mpv_proc is None or self.mpv_proc.poll() is not None:
            self.start_mpv(url)
        else:
            self.mpv_client.load_file(url)
        apply_hdmi_phy_fix()

    def stop_stream(self):
        self.show_dashboard("Stream stopped by user")

    def dashboard_ticker(self):
        while True:
            time.sleep(5)
            apply_hdmi_phy_fix()
            with self.state_lock:
                is_idle = (self.state == "IDLE")
            if is_idle:
                set_fbcon_cursor(False)
                self.dashboard_gen.generate("Ready to receive streams", write_fb0=True)

    def mpv_exit_watchdog(self):
        """If the user quits mpv via 'q' or 'Esc' while streaming, return to dashboard or terminal."""
        while True:
            time.sleep(0.5)
            if self.mpv_proc is not None:
                ret = self.mpv_proc.poll()
                if ret is not None and not self.intentional_mpv_stop:
                    self.mpv_proc = None
                    self.show_dashboard("Stream closed")

    def input_listener(self):
        """
        Monitor Linux /dev/input/event* devices for:
        - Keyboard 'q' (code 16) or 'Esc' (code 1)
        - Touchscreen tap in top-right corner [Terminal] button (x > 70% of max_x, y < 18% of max_y)
        Works on 32-bit ARMhf (16-byte input_event) and 64-bit (24-byte input_event).
        """
        ev_size = struct.calcsize("llHHi")
        fmt = "llHHi"

        def watch_device(dev_path):
            cur_x = 0
            cur_y = 0
            max_x = 1920
            max_y = 1080
            sys_name = os.path.basename(dev_path)
            try:
                uevent = open(f"/sys/class/input/{sys_name}/device/name").read().strip()
                if "Touchscreen" in uevent or "03EB:843C" in uevent:
                    max_x = 4095
                    max_y = 4095
            except Exception:
                pass

            try:
                with open(dev_path, "rb", buffering=0) as f:
                    while True:
                        buf = f.read(ev_size)
                        if not buf or len(buf) < ev_size:
                            break
                        _, _, ev_type, ev_code, ev_val = struct.unpack(fmt, buf)
                        # EV_KEY = 1, key press = 1
                        if ev_type == 1 and ev_val == 1:
                            if ev_code in (1, 16):  # KEY_ESC=1, KEY_Q=16
                                drop_to_terminal("Pressed Q / Esc key")
                        # EV_ABS = 3 (Touchscreen coordinates)
                        elif ev_type == 3:
                            if ev_code in (0, 53):  # ABS_X=0, ABS_MT_POSITION_X=53
                                cur_x = ev_val
                                if cur_x > max_x:
                                    max_x = cur_x
                            elif ev_code in (1, 54):  # ABS_Y=1, ABS_MT_POSITION_Y=54
                                cur_y = ev_val
                                if cur_y > max_y:
                                    max_y = cur_y
                        # Touch press inside Top-Right button (x >= 70%, y <= 18%)
                        if ev_type == 1 and ev_code == 330 and ev_val == 1:  # BTN_TOUCH=330
                            if cur_x >= (max_x * 0.70) and cur_y <= (max_y * 0.18):
                                drop_to_terminal("Tapped [Terminal] button on Touchscreen")
            except Exception:
                pass

        watched = set()
        while True:
            for dev in glob.glob("/dev/input/event*"):
                if dev not in watched:
                    watched.add(dev)
                    threading.Thread(target=watch_device, args=(dev,), daemon=True).start()
            time.sleep(5)

    def airplay_monitor(self):
        was_active = False
        while True:
            time.sleep(1)
            out = subprocess.getoutput(f"ss -Htan '( sport = :{AIRPLAY_PORT} or dport = :{AIRPLAY_PORT} )' 2>/dev/null")
            is_active = ("ESTAB" in out)

            if is_active and not was_active:
                print("[AirPlay] Client connected! Yielding screen to UxPlay...", flush=True)
                was_active = True
                with self.state_lock:
                    self.state = "AIRPLAY"
                self.stop_mpv_if_running()

            elif not is_active and was_active:
                print("[AirPlay] Client disconnected. Restoring dashboard...", flush=True)
                was_active = False
                self.show_dashboard("AirPlay session ended")

    def event_listener(self):
        while True:
            try:
                if not os.path.exists(MPV_SOCKET):
                    time.sleep(1)
                    continue
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(MPV_SOCKET)
                f = s.makefile(encoding="utf-8")
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get("event") == "end-file":
                            reason = event.get("reason", "")
                            with self.state_lock:
                                is_streaming = (self.state == "STREAMING")
                            if is_streaming and reason in ["eof", "error"]:
                                self.show_dashboard(f"Stream ended ({reason})")
                    except Exception:
                        pass
                s.close()
            except Exception:
                time.sleep(2)


receiver = SmartReceiverServer()


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/play":
            url = qs.get("url", [""])[0]
            if url:
                receiver.play_stream(url)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "action": "playing", "url": url}).encode())
                return
            self.send_response(400)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'{"error": "Missing url parameter"}')
            return

        elif parsed.path == "/stop":
            receiver.stop_stream()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "action": "dashboard"}')
            return

        elif parsed.path == "/terminal":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "action": "dropped_to_terminal"}')
            threading.Thread(target=drop_to_terminal, args=("Triggered via Web Controller (/terminal)",), daemon=True).start()
            return

        elif parsed.path == "/screenshot":
            if os.path.exists(DASHBOARD_IMG):
                data = open(DASHBOARD_IMG, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                return

        elif parsed.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            with receiver.state_lock:
                st = receiver.state
                cur = receiver.current_url
            data = {
                "device": "Microsoft Surface 2 (Tegra 4)",
                "state": st,
                "current_url": cur,
                "lan_ip": get_lan_ip(),
                "battery": get_battery_status(),
                "uptime": subprocess.getoutput("uptime -p")
            }
            self.wfile.write(json.dumps(data).encode())
            return

        # Default: Web Controller UI
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()

        lan_ip = get_lan_ip()
        with receiver.state_lock:
            st = receiver.state
            cur = receiver.current_url

        state_badge = f'<span style="color:#10b981;">{st}</span>' if st == "IDLE" else f'<span style="color:#38bdf8;">ACTIVE: {st} ({cur})</span>'

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Surface 2 Smart Display Control</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; display: flex; justify-content: center; }}
        .card {{ background: #1e293b; border-radius: 1rem; border: 1px solid #334155; padding: 2rem; width: 100%; max-width: 620px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }}
        h1 {{ font-size: 1.5rem; margin-top: 0; color: #38bdf8; }}
        .status {{ padding: 0.75rem; background: #0f172a; border-radius: 0.5rem; margin-bottom: 1.5rem; border: 1px solid #334155; font-size: 0.9rem; }}
        input[type="text"] {{ width: 100%; box-sizing: border-box; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 1rem; margin-bottom: 1rem; }}
        .buttons {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
        button {{ flex: 1; padding: 0.75rem; border-radius: 0.5rem; font-size: 1rem; font-weight: 600; cursor: pointer; border: none; }}
        .btn-play {{ background: #0284c7; color: white; }}
        .btn-stop {{ background: #10b981; color: white; }}
        .btn-term {{ background: #f59e0b; color: #0f172a; }}
        .tips {{ margin-top: 1.5rem; font-size: 0.85rem; color: #94a3b8; border-top: 1px solid #334155; padding-top: 1rem; }}
        code {{ background: #0f172a; padding: 0.2rem 0.4rem; border-radius: 0.25rem; color: #38bdf8; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>📺 Surface 2 Smart Display Control</h1>
        <div class="status">Current Status: {state_badge} | Battery: {get_battery_status()}</div>
        <form action="/play" method="get">
            <label style="display:block; margin-bottom:0.5rem; font-weight:500;">Cast RTSP / Video Stream URL:</label>
            <input type="text" name="url" placeholder="rtsp://192.168.x.x:8554/live or http://..." required value="{cur}">
            <div class="buttons">
                <button type="submit" class="btn-play">▶ Play Stream</button>
                <button type="button" class="btn-stop" onclick="fetch('/stop').then(()=>window.location.reload())">⏹ Show Dashboard</button>
                <button type="button" class="btn-term" onclick="fetch('/terminal')">⌨ Drop to Terminal (tty1)</button>
            </div>
        </form>
        <div class="tips">
            <p><strong>Quick Controls & Terminal Shortcuts:</strong></p>
            <p>• Drop to Terminal on device: Press <code>Q</code> / <code>Esc</code> or tap <code>[ Terminal ]</code> top-right</p>
            <p>• Start Dashboard again from terminal: Run <code>dashboard</code></p>
            <p>• Cast via CLI: <code>curl "http://{lan_ip}:{HTTP_PORT}/play?url=rtsp://..."</code> or <code>cast play &lt;url&gt;</code></p>
        </div>
    </div>
</body>
</html>
"""
        self.wfile.write(html.encode("utf-8"))


def handle_sigterm(signum, frame):
    restore_tty1_terminal("Stopped via systemctl / dashboard command")
    os._exit(0)


def main():
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    print("[SmartReceiver] Initializing Surface 2 Dual-Display Dashboard...", flush=True)
    receiver.show_dashboard("Ready to receive streams")

    threading.Thread(target=receiver.dashboard_ticker, daemon=True).start()
    threading.Thread(target=receiver.mpv_exit_watchdog, daemon=True).start()
    threading.Thread(target=receiver.input_listener, daemon=True).start()
    threading.Thread(target=receiver.airplay_monitor, daemon=True).start()
    threading.Thread(target=receiver.event_listener, daemon=True).start()

    print(f"[SmartReceiver] Listening on http://0.0.0.0:{HTTP_PORT} ...", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop_mpv_if_running()
        restore_tty1_terminal("Dashboard service exited")


if __name__ == "__main__":
    main()
