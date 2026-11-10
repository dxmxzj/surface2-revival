#!/usr/bin/env python3
"""
Surface 2 Framebuffer On-Screen Touch Keyboard & Terminal Bar (tty1 / /dev/fb0)
- Automatically runs when the Smart Display Dashboard is stopped (interactive tty1 mode).
- Resizes tty1 console to 38 rows (top 616px of 1080p screen) while visible so terminal output
  never overlaps the touch keyboard.
- Renders a 1080p dark-themed QWERTY + Terminal Symbol + Action Bar keyboard at y=630..1080 on /dev/fb0.
- Reads touch events from the Atmel maXTouch touchscreen (03EB:843C, ABS_X/ABS_Y 0..4095)
  and injects keystrokes directly into /dev/tty1 via TIOCSTI (0x5412).
- Includes one-tap buttons for:
  - [ Launch Dashboard ] -> restarts surface-dashboard.service
  - [ Hide Keyboard ]    -> collapses into a small bottom-right [ Show Keyboard ] touch pill (restoring 64 rows)
"""

import os
import sys
import glob
import time
import fcntl
import struct
import signal
import threading
import subprocess
from PIL import Image, ImageDraw, ImageFont

FB_WIDTH = 1920
FB_HEIGHT = 1080
KBD_TOP = 630
KBD_HEIGHT = FB_HEIGHT - KBD_TOP  # 450px
TIOCSTI = 0x5412
TIOCSWINSZ = 0x5414


def set_tty1_rows(rows: int, cols: int = 240):
    """Set /dev/tty1 character grid size so terminal scrolling stays above the touch keyboard."""
    try:
        fd = os.open("/dev/tty1", os.O_RDWR)
        try:
            # struct winsize: ws_row, ws_col, ws_xpixel, ws_ypixel (4 * uint16)
            ws = struct.pack("HHHH", rows, cols, FB_WIDTH, rows * 16)
            fcntl.ioctl(fd, TIOCSWINSZ, ws)
        finally:
            os.close(fd)
    except Exception:
        pass


def inject_tty1(data: bytes):
    """Inject bytes directly into /dev/tty1 input queue via TIOCSTI."""
    try:
        fd = os.open("/dev/tty1", os.O_RDWR)
        try:
            for b in data:
                fcntl.ioctl(fd, TIOCSTI, bytes([b]))
        finally:
            os.close(fd)
    except Exception as e:
        print(f"[TouchKbd] TIOCSTI error: {e}", flush=True)


class TouchKeyboard:
    def __init__(self):
        self.shift = False
        self.caps = False
        self.ctrl = False
        self.collapsed = False
        self.active_key_id = None
        self.lock = threading.Lock()
        self.running = True

        try:
            self.font_key = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            self.font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
            self.font_bar = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 19)
        except Exception:
            self.font_key = self.font_sm = self.font_bar = ImageFont.load_default()

        self.buttons = []  # List of dicts: {id, x1, y1, x2, y2, label, action}
        self._build_layout()

    def _build_layout(self):
        self.buttons = []
        if self.collapsed:
            # Small bottom-right floating pill: x=1560..1905, y=1015..1072
            self.buttons.append({
                "id": "ACT_EXPAND",
                "x1": 1560, "y1": 1015, "x2": 1905, "y2": 1072,
                "label": "[ Show Touch Keyboard ]",
                "type": "action_amber"
            })
            self.buttons.append({
                "id": "ACT_DASHBOARD",
                "x1": 1260, "y1": 1015, "x2": 1545, "y2": 1072,
                "label": "[ Launch Dashboard ]",
                "type": "action_green"
            })
            return

        # Top Quick-Action Bar (y = 640..692)
        bar_y1, bar_y2 = 640, 692
        actions = [
            ("ACT_DASHBOARD", 20, 360, "[ Launch Dashboard ]", "action_green"),
            ("ACT_STATUS", 375, 615, "[ Appliance Status ]", "action_blue"),
            ("ACT_NET", 630, 850, "[ Wi-Fi / IP ]", "action_blue"),
            ("ACT_CTRLC", 865, 1065, "[ Ctrl+C ]", "action_red"),
            ("ACT_CLEAR", 1080, 1260, "[ Clear ]", "action_slate"),
            ("ACT_COLLAPSE", 1580, 1900, "[ Hide Keyboard ]", "action_amber"),
        ]
        for aid, x1, x2, lbl, btype in actions:
            self.buttons.append({
                "id": aid, "x1": x1, "y1": bar_y1, "x2": x2, "y2": bar_y2,
                "label": lbl, "type": btype
            })

        # 5-Row QWERTY Keyboard (y = 702..1070)
        rows = [
            # Row 0
            [
                ("`", "~", 1.0), ("1", "!", 1.0), ("2", "@", 1.0), ("3", "#", 1.0),
                ("4", "$", 1.0), ("5", "%", 1.0), ("6", "^", 1.0), ("7", "&", 1.0),
                ("8", "*", 1.0), ("9", "(", 1.0), ("0", ")", 1.0), ("-", "_", 1.0),
                ("=", "+", 1.0), ("BACKSPACE", "⌫ Backspace", 2.0)
            ],
            # Row 1
            [
                ("TAB", "Tab ⇥", 1.5), ("q", "Q", 1.0), ("w", "W", 1.0), ("e", "E", 1.0),
                ("r", "R", 1.0), ("t", "T", 1.0), ("y", "Y", 1.0), ("u", "U", 1.0),
                ("i", "I", 1.0), ("o", "O", 1.0), ("p", "P", 1.0), ("[", "{", 1.0),
                ("]", "}", 1.0), ("\\", "|", 1.5)
            ],
            # Row 2
            [
                ("CAPS", "Caps", 1.8), ("a", "A", 1.0), ("s", "S", 1.0), ("d", "D", 1.0),
                ("f", "F", 1.0), ("g", "G", 1.0), ("h", "H", 1.0), ("j", "J", 1.0),
                ("k", "K", 1.0), ("l", "L", 1.0), (";", ":", 1.0), ("'", "\"", 1.0),
                ("ENTER", "⏎ Enter", 2.2)
            ],
            # Row 3
            [
                ("SHIFT", "⇧ Shift", 2.2), ("z", "Z", 1.0), ("x", "X", 1.0), ("c", "C", 1.0),
                ("v", "V", 1.0), ("b", "B", 1.0), ("n", "N", 1.0), ("m", "M", 1.0),
                (",", "<", 1.0), (".", ">", 1.0), ("/", "?", 1.0), ("UP", "↑", 1.3),
                ("SHIFT", "⇧ Shift", 1.5)
            ],
            # Row 4
            [
                ("CTRL", "Ctrl", 1.5), ("ESC", "Esc", 1.3), ("/", "/", 1.0), ("-", "-", 1.0),
                ("SPACE", "Space", 6.3), ("|", "|", 1.0), ("LEFT", "←", 1.3),
                ("DOWN", "↓", 1.3), ("RIGHT", "→", 1.3)
            ]
        ]

        y_start = 702
        row_h = 66
        row_gap = 7
        margin_x = 20
        usable_w = FB_WIDTH - (2 * margin_x)
        key_gap = 8

        for r_idx, row in enumerate(rows):
            y1 = y_start + r_idx * (row_h + row_gap)
            y2 = y1 + row_h
            total_weight = sum(k[2] for k in row)
            cur_x = float(margin_x)
            for k_idx, (norm_val, shift_val, weight) in enumerate(row):
                w = (usable_w * (weight / total_weight))
                x1 = int(round(cur_x))
                x2 = int(round(cur_x + w - key_gap))
                cur_x += w

                upper = self.shift ^ self.caps
                if len(norm_val) == 1 and norm_val.isalpha():
                    disp = shift_val if upper else norm_val
                    out_str = disp
                elif len(norm_val) == 1:
                    disp = shift_val if self.shift else norm_val
                    out_str = disp
                else:
                    disp = shift_val
                    out_str = norm_val

                btype = "key"
                if norm_val in ("ENTER", "BACKSPACE", "SHIFT", "CAPS", "CTRL", "TAB", "ESC"):
                    btype = "mod"
                    if (norm_val == "SHIFT" and self.shift) or (norm_val == "CAPS" and self.caps) or (norm_val == "CTRL" and self.ctrl):
                        btype = "mod_active"

                self.buttons.append({
                    "id": f"R{r_idx}_K{k_idx}_{norm_val}",
                    "key": norm_val,
                    "out": out_str,
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "label": disp,
                    "type": btype
                })

    def render(self):
        with self.lock:
            self._build_layout()
            if self.collapsed:
                # Draw only the bottom-right collapsed bar (y = 1008..1080)
                strip_h = 72
                img = Image.new("RGB", (FB_WIDTH, strip_h), color=(0, 0, 0))
                draw = ImageDraw.Draw(img)
                for b in self.buttons:
                    fill = (30, 41, 59)
                    outline = (245, 158, 11) if b["type"] == "action_amber" else (16, 185, 129)
                    tfill = (251, 191, 36) if b["type"] == "action_amber" else (52, 211, 153)
                    draw.rounded_rectangle(
                        [(b["x1"], b["y1"] - 1008), (b["x2"], b["y2"] - 1008)],
                        radius=10, fill=fill, outline=outline, width=2
                    )
                    draw.text((b["x1"] + 18, b["y1"] - 1008 + 16), b["label"], font=self.font_bar, fill=tfill)
                self._write_fb0_region(img, 1008)
                return

            img = Image.new("RGB", (FB_WIDTH, KBD_HEIGHT), color=(15, 23, 42))
            draw = ImageDraw.Draw(img)
            # Top separator line
            draw.line([(0, 0), (FB_WIDTH, 0)], fill=(56, 189, 248), width=2)
            draw.text((1300, 22), "Surface 2 Touch Keyboard (tty1)", font=self.font_sm, fill=(148, 163, 184))

            for b in self.buttons:
                bx1, by1 = b["x1"], b["y1"] - KBD_TOP
                bx2, by2 = b["x2"], b["y2"] - KBD_TOP
                is_pressed = (b["id"] == self.active_key_id)

                btype = b["type"]
                if is_pressed:
                    fill, outline, tfill = (56, 189, 248), (255, 255, 255), (15, 23, 42)
                elif btype == "action_green":
                    fill, outline, tfill = (6, 78, 59), (16, 185, 129), (52, 211, 153)
                elif btype == "action_blue":
                    fill, outline, tfill = (12, 74, 110), (56, 189, 248), (186, 230, 253)
                elif btype == "action_red":
                    fill, outline, tfill = (127, 29, 29), (248, 113, 113), (254, 202, 202)
                elif btype == "action_amber":
                    fill, outline, tfill = (69, 26, 3), (245, 158, 11), (251, 191, 36)
                elif btype == "action_slate":
                    fill, outline, tfill = (30, 41, 59), (100, 116, 139), (226, 232, 240)
                elif btype == "mod_active":
                    fill, outline, tfill = (2, 132, 199), (56, 189, 248), (255, 255, 255)
                elif btype == "mod":
                    fill, outline, tfill = (51, 65, 85), (100, 116, 139), (226, 232, 240)
                else:
                    fill, outline, tfill = (30, 41, 59), (71, 85, 105), (248, 250, 252)

                draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=8, fill=fill, outline=outline, width=2)
                fnt = self.font_bar if b["id"].startswith("ACT_") else self.font_key
                lbl = b["label"]
                try:
                    bbox = draw.textbbox((0, 0), lbl, font=fnt)
                    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                except Exception:
                    tw, th = len(lbl) * 11, 20
                tx = bx1 + max(6, ((bx2 - bx1) - tw) // 2)
                ty = by1 + max(4, ((by2 - by1) - th) // 2 - 2)
                draw.text((tx, ty), lbl, font=fnt, fill=tfill)

            self._write_fb0_region(img, KBD_TOP)

    def _write_fb0_region(self, img: Image.Image, y_offset: int):
        try:
            r, g, b = img.split()
            a = Image.new("L", img.size, 255)
            bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
            byte_offset = y_offset * FB_WIDTH * 4
            with open("/dev/fb0", "r+b") as fb:
                fb.seek(byte_offset)
                fb.write(bgra)
        except Exception as e:
            print(f"[TouchKbd] fb0 write error: {e}", flush=True)

    def handle_tap(self, px: int, py: int):
        hit = None
        with self.lock:
            for b in self.buttons:
                if b["x1"] <= px <= b["x2"] and b["y1"] <= py <= b["y2"]:
                    hit = b
                    break
        if not hit:
            return

        self.active_key_id = hit["id"]
        self.render()
        time.sleep(0.06)
        self.active_key_id = None

        bid = hit["id"]
        if bid == "ACT_DASHBOARD":
            set_tty1_rows(67)
            subprocess.Popen(["/usr/local/bin/dashboard", "start"])
            os._exit(0)
        elif bid == "ACT_COLLAPSE":
            self.collapsed = True
            set_tty1_rows(62)
            inject_tty1(b"\x0c")  # Ctrl+L to redraw prompt cleanly
            self.render()
            return
        elif bid == "ACT_EXPAND":
            self.collapsed = False
            set_tty1_rows(38)
            inject_tty1(b"\x0c")
            self.render()
            return
        elif bid == "ACT_STATUS":
            inject_tty1(b"cast status\n")
        elif bid == "ACT_NET":
            inject_tty1(b"ip -br a\n")
        elif bid == "ACT_CTRLC":
            inject_tty1(b"\x03")
        elif bid == "ACT_CLEAR":
            inject_tty1(b"clear\n")
        else:
            key = hit.get("key", "")
            out = hit.get("out", "")
            if key == "SHIFT":
                self.shift = not self.shift
            elif key == "CAPS":
                self.caps = not self.caps
            elif key == "CTRL":
                self.ctrl = not self.ctrl
            elif key == "BACKSPACE":
                inject_tty1(b"\x7f")
            elif key == "ENTER":
                inject_tty1(b"\n")
            elif key == "TAB":
                inject_tty1(b"\t")
            elif key == "ESC":
                inject_tty1(b"\x1b")
            elif key == "SPACE":
                inject_tty1(b" ")
            elif key == "UP":
                inject_tty1(b"\x1b[A")
            elif key == "DOWN":
                inject_tty1(b"\x1b[B")
            elif key == "RIGHT":
                inject_tty1(b"\x1b[C")
            elif key == "LEFT":
                inject_tty1(b"\x1b[D")
            elif out:
                if self.ctrl and len(out) == 1 and out.isalpha():
                    ctrl_byte = ord(out.lower()) - ord("a") + 1
                    inject_tty1(bytes([ctrl_byte]))
                    self.ctrl = False
                else:
                    inject_tty1(out.encode("utf-8"))
                if self.shift:
                    self.shift = False

        self.render()


def watch_touchscreen(kbd: TouchKeyboard):
    ev_size = struct.calcsize("llHHi")
    fmt = "llHHi"

    def listen_dev(dev_path):
        cur_x, cur_y = 0, 0
        max_x, max_y = 4095, 4095
        try:
            with open(dev_path, "rb", buffering=0) as f:
                while kbd.running:
                    buf = f.read(ev_size)
                    if not buf or len(buf) < ev_size:
                        break
                    _, _, ev_type, ev_code, ev_val = struct.unpack(fmt, buf)
                    if ev_type == 3:  # EV_ABS
                        if ev_code in (0, 53):
                            cur_x = ev_val
                        elif ev_code in (1, 54):
                            cur_y = ev_val
                    elif ev_type == 1 and ev_code == 330 and ev_val == 1:  # BTN_TOUCH press
                        px = int(cur_x * FB_WIDTH / max_x)
                        py = int(cur_y * FB_HEIGHT / max_y)
                        threading.Thread(target=kbd.handle_tap, args=(px, py), daemon=True).start()
        except Exception:
            pass

    watched = set()
    while kbd.running:
        for dev in glob.glob("/dev/input/event*"):
            if dev not in watched:
                name_file = f"/sys/class/input/{os.path.basename(dev)}/device/name"
                name = open(name_file).read().strip() if os.path.exists(name_file) else ""
                if "Touchscreen" in name or "03EB:843C" in name:
                    watched.add(dev)
                    threading.Thread(target=listen_dev, args=(dev,), daemon=True).start()
        time.sleep(5)


def main():
    kbd = TouchKeyboard()

    def cleanup(signum, frame):
        kbd.running = False
        set_tty1_rows(67)
        os._exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    set_tty1_rows(38)
    kbd.render()
    threading.Thread(target=watch_touchscreen, args=(kbd,), daemon=True).start()

    # Periodically re-render keyboard in case console output redraws
    while kbd.running:
        time.sleep(1.5)
        kbd.render()


if __name__ == "__main__":
    main()
