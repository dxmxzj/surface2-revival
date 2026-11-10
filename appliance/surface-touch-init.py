#!/usr/bin/env python3
"""
Surface 2 Hardware Touchscreen Initializer
- Drives Tegra GPIO 86 (Bank 2, Port K, Pin 6) HIGH to supply VDD power to Atmel 03EB:843C.
- Pulses Tegra GPIO 58 (Bank 1, Port H, Pin 2) LOW for 100ms then HIGH to reset controller
  matching Microsoft Surface 2 ACPI PRGR specification.
- Ensures i2c_hid_of driver is bound to 1-004b so /dev/input/event* is created.
"""
import mmap
import struct
import time
import os

def init_touch():
    try:
        with open("/dev/mem", "r+b") as f:
            mm = mmap.mmap(f.fileno(), 0x1000, offset=0x6000d000)

            def set_gpio(gpio, val):
                bank = gpio >> 5
                port = (gpio >> 3) & 3
                bit = gpio & 7
                reg = bank * 0x100 + port * 4
                mask_val = (1 << (bit + 8)) | ((1 if val else 0) << bit)
                # CNF = 1 (GPIO)
                mm[reg + 0x80 + 0x00 : reg + 0x80 + 0x04] = struct.pack("<I", (1 << (bit + 8)) | (1 << bit))
                # OE = 1 (Output)
                mm[reg + 0x80 + 0x10 : reg + 0x80 + 0x14] = struct.pack("<I", (1 << (bit + 8)) | (1 << bit))
                # OUT = val
                mm[reg + 0x80 + 0x20 : reg + 0x80 + 0x24] = struct.pack("<I", mask_val)

            # 1. Power ON VDD: GPIO 86 HIGH
            set_gpio(86, 1)
            time.sleep(0.07)  # 70ms ramp from ACPI PRTU

            # 2. Reset pulse: GPIO 58 LOW for 100ms then HIGH
            set_gpio(58, 0)
            time.sleep(0.10)  # 100ms reset from ACPI PRGR
            set_gpio(58, 1)
            time.sleep(0.05)

        print("[SurfaceTouchInit] Hardware rails powered: GPIO 86=1, GPIO 58=1", flush=True)
    except Exception as e:
        print(f"[SurfaceTouchInit] GPIO access failed: {e}", flush=True)

    # Rebind i2c_hid_of if device not present or to ensure fresh start
    dev_path = "/sys/bus/i2c/devices/1-004b"
    driver_bind = "/sys/bus/i2c/drivers/i2c_hid_of/bind"
    driver_unbind = "/sys/bus/i2c/drivers/i2c_hid_of/unbind"

    if not os.path.exists(os.path.join(dev_path, "driver")):
        if os.path.exists(driver_bind):
            try:
                open(driver_bind, "w").write("1-004b")
                print("[SurfaceTouchInit] Bound 1-004b to i2c_hid_of", flush=True)
            except Exception as e:
                print(f"[SurfaceTouchInit] Bind error: {e}", flush=True)

if __name__ == "__main__":
    init_touch()
