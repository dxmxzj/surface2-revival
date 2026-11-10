#!/usr/bin/env python3
"""
Build the Golden Device Tree Blob (tegra114-microsoft-surface-2.dtb, 22,395 bytes)
for the Microsoft Surface 2 (Tegra 4 / T114) running Linux 5.17+.

Fixes applied on top of the base Surface 2 DTB (dtb-21906.dtb):
1. Keeps active-high panel@0:reset-gpios = <0x05 0x56 0x00>, /backlight:pwms, and
   pmic@58 ldoln (always-on, boot-on) so the 1080p Samsung 106HL02 DSI LCD panel stays ON.
2. Removes interrupt-parent, interrupts, and charger subnode from /i2c@7000d000/pmic@48
   (tps65090) so the kernel never triggers a shared GIC IRQ #96 storm ("Disabling IRQ #96").
3. Adds regulator-always-on and regulator-boot-on to dcdc3, regen1, regen2, regulator-usb3,
   touchscreen_vdd, and what_ever (GPIO 0x3a) so regulator_init_complete_work never cuts
   power to the Marvell SD8897 Wi-Fi chip or USB 2.0 VBUS rail 30-60 seconds after boot.
4. Enables /host1x@50000000/hdmi@54280000 (status = "okay", pll/vdd/hdmi supplies, HPD GPIO),
   adds /pinmux@70000868/pinmux/ddc (ddc_scl_pv4, ddc_sda_pv5 -> i2c4 with input enabled),
   and adds ldo4 (avdd-hdmi) under pmic@58 so Micro-HDMI outputs 1920x1080@60.
"""

import os
import sys
import struct


def parse_fdt_tree(d: bytes):
    magic, totalsize, off_struct, off_strs, off_mem_rsv, version, last_comp_version, boot_cpuid = struct.unpack(
        ">IIIIIIII", d[:32]
    )
    assert magic == 0xD00DFEED, f"Invalid FDT magic: {hex(magic)}"
    rsv_end = off_mem_rsv
    while True:
        addr, size = struct.unpack(">QQ", d[rsv_end : rsv_end + 16])
        rsv_end += 16
        if addr == 0 and size == 0:
            break
    mem_rsv = d[off_mem_rsv:rsv_end]

    pos = off_struct
    stack = []
    root = None
    while True:
        tok = struct.unpack(">I", d[pos : pos + 4])[0]
        pos += 4
        if tok == 1:
            end = d.find(b"\x00", pos)
            name = d[pos:end].decode("ascii")
            pos = (end + 4) & ~3
            node = {"name": name, "props": [], "children": []}
            if stack:
                stack[-1]["children"].append(node)
            else:
                root = node
            stack.append(node)
        elif tok == 2:
            stack.pop()
        elif tok == 3:
            plen, nameoff = struct.unpack(">II", d[pos : pos + 8])
            pos += 8
            val = d[pos : pos + plen]
            pos = (pos + plen + 3) & ~3
            end = d.find(b"\x00", off_strs + nameoff)
            pname = d[off_strs + nameoff : end].decode("ascii")
            stack[-1]["props"].append([pname, val])
        elif tok == 4:
            pass
        elif tok == 9:
            break
    return root, mem_rsv, version, last_comp_version, boot_cpuid


def serialize_fdt_tree(root, mem_rsv, version=17, last_comp_version=16, boot_cpuid=0) -> bytes:
    strs = bytearray()
    str_offs = {}

    def get_str_off(s):
        if s not in str_offs:
            str_offs[s] = len(strs)
            strs.extend(s.encode("ascii") + b"\x00")
        return str_offs[s]

    struct_buf = bytearray()

    def emit_node(node):
        struct_buf.extend(struct.pack(">I", 1))
        nb = node["name"].encode("ascii") + b"\x00"
        while len(nb) % 4 != 0:
            nb += b"\x00"
        struct_buf.extend(nb)
        for pname, val in node["props"]:
            noff = get_str_off(pname)
            struct_buf.extend(struct.pack(">III", 3, len(val), noff))
            vb = bytes(val)
            while len(vb) % 4 != 0:
                vb += b"\x00"
            struct_buf.extend(vb)
        for ch in node["children"]:
            emit_node(ch)
        struct_buf.extend(struct.pack(">I", 2))

    emit_node(root)
    struct_buf.extend(struct.pack(">I", 9))

    off_mem_rsv = 40
    off_struct = (off_mem_rsv + len(mem_rsv) + 3) & ~3
    off_strs = off_struct + len(struct_buf)
    totalsize = off_strs + len(strs)

    hdr = struct.pack(
        ">IIIIIIIIII",
        0xD00DFEED,
        totalsize,
        off_struct,
        off_strs,
        off_mem_rsv,
        version,
        last_comp_version,
        boot_cpuid,
        len(strs),
        len(struct_buf),
    )
    out = bytearray(hdr)
    out.extend(mem_rsv)
    while len(out) < off_struct:
        out.append(0)
    out.extend(struct_buf)
    out.extend(strs)
    return bytes(out)


def ensure_prop(node, name, val=b""):
    for p in node["props"]:
        if p[0] == name:
            p[1] = val
            return
    node["props"].append([name, val])


def build_golden_dtb(base_dtb_path: str, out_dtb_path: str):
    root, mem_rsv, ver, lver, cpuid = parse_fdt_tree(open(base_dtb_path, "rb").read())

    # 1. PMIC fixes (/i2c@7000d000/pmic@48 & pmic@58)
    for ch in root["children"]:
        if ch["name"] == "i2c@7000d000":
            for pmic in ch["children"]:
                if pmic["name"] == "pmic@48":
                    pmic["props"] = [p for p in pmic["props"] if p[0] not in ("interrupt-parent", "interrupts")]
                    pmic["children"] = [c for c in pmic["children"] if c["name"] != "charger"]
                    for sub in pmic["children"]:
                        if sub["name"] == "regulators":
                            for reg in sub["children"]:
                                if reg["name"] == "dcdc3":
                                    ensure_prop(reg, "regulator-always-on", b"")
                                    ensure_prop(reg, "regulator-boot-on", b"")
                elif pmic["name"] == "pmic@58":
                    for sub in pmic["children"]:
                        if sub["name"] == "pmic":
                            for rnode in sub["children"]:
                                if rnode["name"] == "regulators":
                                    for reg in rnode["children"]:
                                        if reg["name"] in ("regen1", "regen2", "ldoln"):
                                            ensure_prop(reg, "regulator-always-on", b"")
                                            ensure_prop(reg, "regulator-boot-on", b"")
                                    if not any(r["name"] == "ldo4" for r in rnode["children"]):
                                        rnode["children"].append(
                                            {
                                                "name": "ldo4",
                                                "props": [
                                                    ["regulator-name", b"avdd-hdmi\x00"],
                                                    ["regulator-always-on", b""],
                                                    ["regulator-boot-on", b""],
                                                ],
                                                "children": [],
                                            }
                                        )

    # 2. Top-level fixed regulators (what_ever, regulator-usb3, touchscreen_vdd)
    for ch in root["children"]:
        if ch["name"] in ("what_ever", "regulator-usb3", "touchscreen_vdd"):
            ensure_prop(ch, "regulator-always-on", b"")
            ensure_prop(ch, "regulator-boot-on", b"")

    # 3. Enable DDC I2C4 pinmux for HDMI EDID
    for ch in root["children"]:
        if ch["name"] == "pinmux@70000868":
            for sub in ch["children"]:
                if sub["name"] == "pinmux":
                    if not any(p["name"] == "ddc" for p in sub["children"]):
                        sub["children"].append(
                            {
                                "name": "ddc",
                                "props": [
                                    ["nvidia,pins", b"ddc_scl_pv4\x00ddc_sda_pv5\x00"],
                                    ["nvidia,function", b"i2c4\x00"],
                                    ["nvidia,pull", struct.pack(">I", 0)],
                                    ["nvidia,tristate", struct.pack(">I", 0)],
                                    ["nvidia,enable-input", struct.pack(">I", 1)],
                                    ["nvidia,lock", struct.pack(">I", 0)],
                                ],
                                "children": [],
                            }
                        )

    # 4. Enable /host1x@50000000/hdmi@54280000
    for ch in root["children"]:
        if ch["name"] == "host1x@50000000":
            for sub in ch["children"]:
                if sub["name"] == "hdmi@54280000":
                    ensure_prop(sub, "status", b"okay\x00")
                    ensure_prop(sub, "nvidia,ddc-i2c-bus", struct.pack(">I", 0x04))
                    ensure_prop(sub, "nvidia,hpd-gpio", struct.pack(">III", 0x05, 0x6F, 0x00))
                    ensure_prop(sub, "pll-supply", struct.pack(">I", 0x07))
                    ensure_prop(sub, "vdd-supply", struct.pack(">I", 0x10))
                    ensure_prop(sub, "hdmi-supply", struct.pack(">I", 0x18))

    out = serialize_fdt_tree(root, mem_rsv, ver, lver, cpuid)
    open(out_dtb_path, "wb").write(out)
    print(f"Built Golden DTB: {out_dtb_path} ({len(out)} bytes)")


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dtb-21906.dtb"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "boot",
        "tegra114-microsoft-surface-2.dtb",
    )
    build_golden_dtb(base, out)
