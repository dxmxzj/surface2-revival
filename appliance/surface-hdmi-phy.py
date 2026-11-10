#!/usr/bin/env python3
import mmap, os, struct
try:
    st = '/sys/class/drm/card0-HDMI-A-1/status'
    en = '/sys/class/drm/card0-HDMI-A-1/enabled'
    if os.path.exists(st) and open(st).read().strip() != 'connected':
        raise SystemExit(0)
    if os.path.exists(en) and open(en).read().strip() != 'enabled':
        raise SystemExit(0)
    fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
    car = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x60006000)
    rst_h = struct.unpack('<I', car[0x008:0x00c])[0]
    clk_h = struct.unpack('<I', car[0x014:0x018])[0]
    car.close()
    if ((clk_h >> 19) & 1) == 0 or ((rst_h >> 19) & 1) != 0:
        os.close(fd)
        raise SystemExit(0)
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x54280000)
    for reg, val in [(0x57, 0x01003f10), (0x58, 0x00300f00), (0x99, 0x00000000), (0x7e, 0x303f3f3f), (0xd1, 0x040f0f0f)]:
        m[reg*4:reg*4+4] = struct.pack('<I', val)
    m.close()
    os.close(fd)
except Exception:
    pass
