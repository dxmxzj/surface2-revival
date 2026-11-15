#!/usr/bin/env python3
"""Power-cycle TPS65090 FET4 (panel@0 samsung,106hl02) to unlatch black-screen state."""

import argparse
import ctypes
import fcntl
import glob
import os
import struct
import time

DRM_IOCTL_SET_MASTER = 0x0000641E
DRM_IOCTL_DROP_MASTER = 0x0000641F
DRM_IOCTL_MODE_GETCRTC = 0xC06864A1
DRM_IOCTL_MODE_SETCRTC = 0xC06864A2
I2C_RDWR = 0x0707
TPS65090_ADDR = 0x48
TPS65090_REG_FET4_CTRL = 0x12
DSI_CRTC_ID = 64
DSI_CONN_ID = 101


class I2cMsg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
    ]


class I2cRdwrIoctlData(ctypes.Structure):
    _fields_ = [
        ("msgs", ctypes.POINTER(I2cMsg)),
        ("nmsgs", ctypes.c_uint32),
    ]


def find_palmas_i2c_bus():
    for path in sorted(glob.glob("/sys/bus/i2c/devices/i2c-*")):
        if "7000d000" in os.path.realpath(path):
            return f"/dev/{os.path.basename(path)}"
    return "/dev/i2c-4"


def i2c_read(fd, reg):
    wbuf = (ctypes.c_uint8 * 1)(reg)
    rbuf = (ctypes.c_uint8 * 1)(0)
    msgs = (I2cMsg * 2)(
        I2cMsg(TPS65090_ADDR, 0, 1, wbuf),
        I2cMsg(TPS65090_ADDR, 1, 1, rbuf),
    )
    fcntl.ioctl(fd, I2C_RDWR, I2cRdwrIoctlData(msgs, 2))
    return rbuf[0]


def i2c_write(fd, reg, val):
    wbuf = (ctypes.c_uint8 * 2)(reg, val)
    msgs = (I2cMsg * 1)(I2cMsg(TPS65090_ADDR, 0, 2, wbuf))
    fcntl.ioctl(fd, I2C_RDWR, I2cRdwrIoctlData(msgs, 1))


def set_fet4(enabled):
    bus = find_palmas_i2c_bus()
    fd = os.open(bus, os.O_RDWR)
    try:
        cur = i2c_read(fd, TPS65090_REG_FET4_CTRL)
        nxt = (cur | 0x01) if enabled else (cur & ~0x01)
        if nxt != cur:
            i2c_write(fd, TPS65090_REG_FET4_CTRL, nxt)
        return cur, i2c_read(fd, TPS65090_REG_FET4_CTRL)
    finally:
        os.close(fd)


def get_crtc(drm_fd, crtc_id):
    buf = bytearray(0x68)
    struct.pack_into("<I", buf, 12, crtc_id)
    fcntl.ioctl(drm_fd, DRM_IOCTL_MODE_GETCRTC, buf)
    fb_id = struct.unpack_from("<I", buf, 16)[0]
    mode_valid = struct.unpack_from("<I", buf, 32)[0]
    return fb_id, mode_valid, bytes(buf[36:104])


def set_crtc(drm_fd, crtc_id, conn_id, fb_id, mode_bytes):
    buf = bytearray(0x68)
    if conn_id and fb_id:
        ca = (ctypes.c_uint32 * 1)(conn_id)
        struct.pack_into(
            "<QIIIIIIIII", buf, 0, ctypes.addressof(ca), 1, crtc_id, fb_id, 0, 0, 0, 1, 0, 0
        )
        buf[36:104] = mode_bytes
    else:
        struct.pack_into("<QIIIIIIIII", buf, 0, 0, 0, crtc_id, 0, 0, 0, 0, 0, 0, 0)
    fcntl.ioctl(drm_fd, DRM_IOCTL_MODE_SETCRTC, buf)


def reset_panel(off_seconds=2.5):
    drm_fd = None
    fb_id = 0
    mode_valid = 0
    mode_bytes = b""
    master = False

    if os.path.exists("/dev/dri/card0"):
        drm_fd = os.open("/dev/dri/card0", os.O_RDWR)
        try:
            fcntl.ioctl(drm_fd, DRM_IOCTL_SET_MASTER, 0)
            master = True
            fb_id, mode_valid, mode_bytes = get_crtc(drm_fd, DSI_CRTC_ID)
            set_crtc(drm_fd, DSI_CRTC_ID, 0, 0, b"")
            time.sleep(0.15)
        except OSError as exc:
            print(f"[PanelReset] DRM master unavailable ({exc}); cycling FET4 directly", flush=True)

    try:
        before, after_off = set_fet4(False)
        print(f"[PanelReset] TPS65090 FET4 0x{before:02x} -> 0x{after_off:02x} ({off_seconds}s)", flush=True)
        time.sleep(off_seconds)
        _, after_on = set_fet4(True)
        print(f"[PanelReset] TPS65090 FET4 restored -> 0x{after_on:02x}", flush=True)
        time.sleep(0.15)
    finally:
        if drm_fd is not None:
            if master:
                if fb_id and mode_valid:
                    set_crtc(drm_fd, DSI_CRTC_ID, DSI_CONN_ID, fb_id, mode_bytes)
                fcntl.ioctl(drm_fd, DRM_IOCTL_DROP_MASTER, 0)
            os.close(drm_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--off-only", action="store_true", help="Turn TPS65090 FET4 off and exit")
    parser.add_argument("--wait", type=float, default=2.5, help="Seconds to keep FET4 off")
    args = parser.parse_args()

    if args.off_only:
        before, after = set_fet4(False)
        print(f"[PanelReset] FET4 off: 0x{before:02x} -> 0x{after:02x}", flush=True)
        return

    reset_panel(off_seconds=args.wait)


if __name__ == "__main__":
    main()
