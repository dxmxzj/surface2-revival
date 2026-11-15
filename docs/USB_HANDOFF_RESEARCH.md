# Microsoft Surface 2 (Tegra 4 / T114) — External USB 2.0 Host Port Handoff Research

## 1. Problem Statement
When booting Linux `5.17.0-rc3-next-20220207` (`boot.efi`) from a USB flash drive via UEFI on the **Microsoft Surface 2 (Model 1572, NVIDIA Tegra 4 / T114)**:
- UEFI loads `boot.efi`, `tegra114-microsoft-surface-2.dtb`, and `initrd.gz` from the external full-size USB 3.0 Type-A port using the **Tegra 114 XUSB (`xHCI` @ `0x70090000` / `XUSB_PADCTL` @ `0x7009f000`)** controller.
- Linux `5.17.0-rc3-next-20220207` has `CONFIG_USB_TEGRA_XHCI=n` (no Tegra 114 XUSB driver or firmware in mainline) and instead drives USB 2.0 via the ChipIdea/EHCI controller **`usb@7d000000` (`usb1`)** and **`usb-phy@7d000000`** (`drivers/usb/chipidea/ci_hdrc_tegra.c` + `drivers/usb/phy/phy-tegra-usb.c`).
- Because UEFI leaves the external USB port's pad mux and bias pad owned by `XUSB_PADCTL` (`0x7009f000`), Linux's `usb1` (`0x7d000000`) cannot enumerate the USB flash drive (`/dev/sda1`) to locate `surface2-rootfs-golden.tar.gz` unless a complete hardware handoff is performed from `XUSB_PADCTL` to `USB1` (`SNPS`).

---

## 2. Reference Kernel & Firmware Sources (`/tmp/`)
All relevant Tegra 114 register definitions and vendor driver sequences have been extracted to `/tmp/`:
- `/tmp/nv_xhci_tegra.c` — NVIDIA L4T Tegra 114 / 124 XUSB host & padctl driver (`tegra114_padctl_offsets` at lines 369–386, `tegra_xhci_war_for_tctrl_rctrl` at lines 3105–3174).
- `/tmp/tegra11x_usb_phy.c` — NVIDIA L4T Tegra 114 USB2 UTMIP PHY driver (`utmi_phy_power_on` at lines 972–1205).
- `/tmp/tegra_usb_pmc.c` & `/tmp/tegra_usb_pmc.h` — NVIDIA L4T Tegra 114 USB PMC sleepwalk & bias tracking driver (`utmi_phy_set_snps_trking_data` at lines 107–240).
- `/tmp/tegra_usb_pad_ctrl.c` & `/tmp/tegra_usb_pad_ctrl.h` — NVIDIA L4T XUSB_PADCTL & UTMIP pad control driver (`utmi_phy_pad_enable` at lines 618–646).
- `/tmp/phy-tegra-usb.c` — Mainline Linux 5.17 Tegra USB PHY driver running inside `boot.efi`.
- `/tmp/ci_hdrc_tegra.c` — Mainline Linux 5.17 Tegra ChipIdea/EHCI wrapper running inside `boot.efi`.
- `/tmp/tegra114.dtsi` — Upstream Tegra 114 device tree (`usb@7d000000` at lines 729–765).
- `/tmp/SurfaceRT2.dsdt.txt` & `/tmp/ssdt0.dsl` — Decompiled Surface 2 ACPI DSDT/SSDT tables (`USB1` at `0x7d000000`, `XUSB` at `0x70090000`, `GPIO PK1` VBUS).

---

## 3. Verified Hardware Architecture & Register Map (Tegra 114 vs Tegra 124/20)

### 3.1. Critical Tegra 114 vs Tegra 124 / Tegra 20 Register Differences
1. **`XUSB_PADCTL` (`0x7009f000`) Offsets (`nv_xhci_tegra.c` lines 369–386):**
   - `XUSB_PADCTL_USB2_PAD_MUX_0` = **`0x004`** (`0x7009f004`)
   - `XUSB_PADCTL_USB2_PORT_CAP_0` = **`0x008`** (`0x7009f008`)
   - `XUSB_PADCTL_SNPS_OC_MAP_0` = **`0x00c`** (`0x7009f00c`)
   - `XUSB_PADCTL_USB2_OC_MAP_0` = **`0x010`** (`0x7009f010`)
   - `XUSB_PADCTL_OC_DET_0` = **`0x018`** (`0x7009f018`)
   - `XUSB_PADCTL_ELPG_PROGRAM_0` = **`0x01c`** (`0x7009f01c`)
   - `XUSB_PADCTL_USB2_OTG_PAD0_CTL_0` = **`0x090`** (`0x7009f090`) *(NOT `0xa0`, which is Tegra 124!)*
   - `XUSB_PADCTL_USB2_OTG_PAD1_CTL_0` = **`0x094`** (`0x7009f094`)
   - `XUSB_PADCTL_USB2_OTG_PAD0_CTL_1` = **`0x098`** (`0x7009f098`) *(NOT `0xac`, which is Tegra 124!)*
   - `XUSB_PADCTL_USB2_OTG_PAD1_CTL_1` = **`0x09c`** (`0x7009f09c`)
   - `XUSB_PADCTL_USB2_BIAS_PAD_CTL_0` = **`0x0a0`** (`0x7009f0a0`) *(NOT `0xb8`, which is Tegra 124!)*
   - `XUSB_PADCTL_USB2_BIAS_PAD_CTL_1` = **`0x0a4`** (`0x7009f0a4`)
2. **`USB1` (`0x7d000000`) EHCI Operational Registers (`tegra_usb_pmc.h` line 26 & `SurfaceRT2.dsdt.txt` line 1483):**
   - `CAPLENGTH` at `0x7d000100` is `0x30` (NOT `0x40` as on Tegra 20).
   - `USBCMD` = **`0x7d000130`**
   - `USBSTS` = **`0x7d000134`**
   - `PORTSC1` = **`0x7d000174`** *(NOT `0x7d000184`, which is Tegra 20 only!)*
   - `HOSTPC1_DEVLC` = **`0x7d0001b4`**
   - `USBMODE` = **`0x7d0001f8`**

---

## 4. Progression of Live Hardware Tests & Register Dumps

### Test 1 (Baseline — Before Pad Mux Fix)
- **Symptom:** `hub 1-0:1.0: 1 port detected`, then `echo 7d000000.usb > .../unbind` permanently disabled `usb1` (`usb usb1: USB disconnect, device number 1`).
- **Registers Captured:**
  - `XUSB_PADCTL_USB2_PAD_MUX (0x7009f004)`: **`0x4`** (`USB2_OTG_PAD_PORT1 = 01 (XUSB)`, `PAD_PORT0 = 00 (SNPS)`)
  - `USB1 PORTSC1 (wrong addr 0x7d000184)`: `0x0`
  - `USB1 HOSTPC1_DEVLC (0x7d0001b4)`: `0x0`
  - `USB1 USB_SUSP_CTRL (0x7d000400)`: `0x11C0` (`UTMIP_PHY_ENABLE | USB_PHY_CLK_VALID | USB_CLKEN`)
  - `USB1 VBUS_SENSORS (0x7d000404)`: `0x06000600` (`A_VBUS_VLD | A_SESS_VLD | B_SESS_VLD`)
- **Lesson Learned:**
  1. Never unbind `7d000000.usb` (`ci_hdrc_tegra_remove` calls `pm_runtime_force_suspend()` which leaves runtime PM disabled on re-bind).
  2. In `tegra_xhci_war_for_tctrl_rctrl()` (`nv_xhci_tegra.c` line 3126), UTMI Pad 0 and Pad 1 share the Bias Pad. Writing `0x0` to `0x7009f004` is required so SNPS owns both pads.

### Test 2 (Commit `52b3640` — Pad Mux `0x4 -> 0x0`, `BIAS_MASTER_PROG_VAL = 1`, `nvidia,needs-double-reset` set)
- **Symptom:**
  - Console printed `XUSB_PADCTL_USB2_PAD_MUX: 0x4 -> 0x0`.
  - **`usb1-port1` physically detected the USB flash drive!**
  - During port reset, kernel repeatedly printed:
    `usb1-port1: cannot reset (err = -110)`
    `usb1-port1: cannot enable. Maybe the USB cable is bad?`
- **Registers Captured:**
  1. `XUSB_PADCTL_USB2_PAD_MUX (0x7009f004)`: **`0x0`**
  2. `XUSB_PADCTL_ELPG_PROGRAM (0x7009f01c)`: **`0x770000`**
  3. `XUSB_PADCTL_OTG_PAD0_CTL0(0x7009f090)`: **`0xC809`** (`HS_CURR_LEVEL = 0x09`, `PD = 0`, `PD2 = 0`, `PD_ZI = 0`)
  4. `XUSB_PADCTL_BIAS_PAD_CTL0(0x7009f0a0)`: **`0x14`** (`HS_DISCON_LEVEL = 5`, `HS_SQUELCH_LEVEL = 0`, `PD = 0`, `PD_TRK = 0`)
  5. `USB1 PORTSC1 (wrong addr 0x7d000184)`: **`0x0`**
  6. `USB1 HOSTPC1_DEVLC       (0x7d0001b4)`: **`0x6000202`** (`PSPD = 3`, `PHCD = 0`, `PTS = 0`)
  7. `USB1 USB_SUSP_CTRL       (0x7d000400)`: **`0x10C0`** (`UTMIP_PHY_ENABLE | USB_PHY_CLK_VALID | USB_CLKEN`)
  8. `USB1 VBUS_SENSORS        (0x7d000404)`: **`0x6000602`** (`A_VBUS_VLD | A_SESS_VLD | B_SESS_VLD`)
  9. `USB1 UTMIP_XCVR_CFG0     (0x7d000808)`: **`0x6002309`** (`HSSLEW_MSB = 3`, `LSRSLEW = 3`, `XCVR_SETUP = 9`)
  10. `USB1 UTMIP_BIAS_CFG0     (0x7d00080c)`: **`0x1C00006`** (`HSDISCON_LEVEL_MSB = 1`, `HSDISCON_LEVEL = 1`, `HSSQUELCH_LEVEL = 2`, `OTGPD = 0`, `BIASPD = 0`)
- **Why Test 2 Detected the Drive on `usb1-port1` But Failed Reset (`-110`):**
  1. `cannot reset (err = -110)` comes from `tegra_usb_internal_port_reset()` in `ci_hdrc_tegra.c` (enabled by `nvidia,needs-double-reset`), which only pulses `PORT_RESET` for 10 ms instead of 50 ms and returns `-ETIMEDOUT` (`-110`) if `PORT_PE` (bit 2 of `PORTSC1` `0x7d000174`) does not set.
  2. In Commit `52b3640`, we set `BIAS_MASTER_PROG_VAL = 1` (`0x7000e670 |= 0x2`), `FUSE_ATERM_SEL = 1` (`0x7d000834 |= 0x10`), and `PD_TRK = 1` (`0x7d00083c = 0x29`), **which allowed `usb1-port1` to detect `CCS = 1`**, BUT we omitted lines 219–232 of `tegra_usb_pmc.c` (reading `UTMIP_BIAS_STS0` `0x7d000840` and writing `PMC_TCTRL_VAL | PMC_RCTRL_VAL` into `PMC_UTMIP_TERM_PAD_CFG` `0x7000e5f8`), AND we ran `tegra_usb_mux_handoff` every 1 second during the scan loop while `khubd` was resetting the port!

### Test 3 (Commit `904c7e2` — Removed `nvidia,needs-double-reset`, Cleared `BIAS_MASTER_PROG_VAL = 0` & `FUSE_ATERM_SEL = 0`)
- **Symptom:**
  - Left scanning with dots (`...`), no `cannot reset (err = -110)` message because `CCS` on `PORTSC1` (`0x7d000174`) read `0x1000` (`PORT_POWER = 1, CCS = 0`).
- **Registers Captured:**
  1. `XUSB_PADCTL_USB2_PAD_MUX (0x7009f004)`: **`0x0`**
  2. `XUSB_PADCTL_ELPG_PROGRAM (0x7009f01c)`: **`0x770000`**
  3. `XUSB_PADCTL_OTG_PAD0_CTL0(0x7009f090)`: **`0xC809`** (reported as `0xC309`)
  4. `XUSB_PADCTL_OTG_PAD0_CTL1(0x7009f098)`: **`0x23`** (`TERM_RANGE_ADJ = 4`, `PD_DR = 0`, `PD_DISC_FORCE_POWERUP = 1`, `PD_CHRP_FORCE_POWERUP = 1`)
  5. `USB1 PORTSC1             (0x7d000174)`: **`0x1000`** (`PORT_POWER = 1`, `CCS = 0`)
  6. `USB1 HOSTPC1_DEVLC       (0x7d0001b4)`: **`0x6000202`**
  7. `USB1 USB_SUSP_CTRL       (0x7d000400)`: **`0x10C0`**
  8. `USB1 UTMIP_XCVR_CFG0     (0x7d000808)`: **`0x6002309`**
  9. `USB1 UTMIP_XCVR_CFG1     (0x7d000838)`: **`0x108200`** (`TERM_RANGE_ADJ = 4 << 18`)
  10. `USB1 UTMIP_BIAS_STS0     (0x7d000840)`: **`0xFF01FF`** (`0x00FF01FF` -> `TCTRL_RAW = 0x00FF` (`ffz = 8 -> utmip_tctrl_val = 0x17`), `RCTRL_RAW = 0x01FF` (`ffz = 9 -> utmip_rctrl_val = 0x18`), so `PMC_UTMIP_TERM_PAD_CFG (0x7000e5f8) = (0x17 << 5) | 0x18 = 0x2F8`!)
- **Crucial Discovery from Comparing Test 2 vs Test 3:**
  - Look at why Test 2 (`commit 52b3640`) detected `CCS = 1` on `usb1-port1` while Test 3 (`commit 904c7e2`) read `PORTSC1 = 0x1000` (`CCS = 0`):
    1. In Test 2, `PMC_UTMIP_BIAS_MASTER_CNTRL` (`0x7000e670`) had **`BIAS_MASTER_PROG_VAL = 1` (`| 0x2`)**, `UTMIP_BIAS_CFG1` (`0x7d00083c`) had **`PD_TRK = 1` (`0x29`)**, and `UTMIP_SPARE_CFG0` (`0x7d000834`) had **`FUSE_ATERM_SEL = 1` (`| 0x10`)** — matching `tegra11x_usb_phy.c` line 1138 and `tegra_usb_pmc.c` lines 215–226!
    2. In Test 3, we cleared `BIAS_MASTER_PROG_VAL = 0` at the end of `utmi_phy_set_snps_trking_data` and cleared `FUSE_ATERM_SEL = 0` in `0x7d000834`, AND at `attempt = 20` `tegra_usb_vbus_cycle` pulsed VBUS!
    3. Furthermore, Test 3 gave us the **exact live value of `UTMIP_BIAS_STS0` (`0x7d000840`) on this Surface 2: `0x00FF01FF`**!
       - `UTMIP_RCTRL_VAL(0x00FF01FF) = 0x01FF` -> `ffz(0x01FF) = 9` -> `utmip_rctrl_val = 0xf + 9 = 0x18` (24)
       - `UTMIP_TCTRL_VAL(0x00FF01FF) = 0x00FF` -> `ffz(0x00FF) = 8` -> `utmip_tctrl_val = 0xf + 8 = 0x17` (23)
       - `PMC_UTMIP_TERM_PAD_CFG (0x7000e5f8) = (0x17 << 5) | 0x18 = 0x000002F8`!
    4. Also look at `XUSB_PADCTL_OTG_PAD0_CTL1 (0x7009f098) = 0x23`:
       - Bit 0 (`USB2_OTG_PD_CHRP_FORCE_POWERUP = 1`) and Bit 1 (`USB2_OTG_PD_DISC_FORCE_POWERUP = 1`) were set by UEFI in `XUSB_PADCTL_OTG_PAD0_CTL1` (`0x7009f098`)!
       - Wait: in `tegra_usb_pad_ctrl.c` lines 405–420 (`xusb_utmi_pad_deinit`), when XUSB owns Pad 0, UEFI sets `USB2_OTG_PD_CHRP_FORCE_POWERUP` (bit 0) and `USB2_OTG_PD_DISC_FORCE_POWERUP` (bit 1) in `0x7009f098`! And what about `XUSB_PADCTL_USB2_PORT_CAP_0` (`0x7009f008`)? If `0x7009f008` still has Port 0 set to `XUSB_HOST_MODE` (`0x1`), does XUSB_PADCTL still intercept Chirp/Disconnect unless `0x7009f008` is cleared to `0x0` and `0x7009f098` bits `[1:0]` are cleared?!
       - AND what about `XUSB_PADCTL` pad mux (`0x7009f004`) when `7d000000.usb` probes?
         When the kernel boots, `phy-tegra-usb.c` (`7d000000.usb-phy`) and `ci_hdrc_tegra.c` (`7d000000.usb`) probe at `~1.2s` into kernel boot — **WHILE `0x7009f004` IS STILL `0x4` AND `XUSB` STILL OWNS THE BIAS PAD!**
         So when `phy-tegra-usb.c` runs `utmip_pad_open()` (which pulses `CAR` reset 22 `USBD` to reset the UTMIP pads) and `utmi_phy_power_on()` at kernel boot, the pad mux hasn't been handed off yet!

---

## 5. Summary of Next Investigation Avenues for Next Agent
1. **Combine Test 2's working `CCS = 1` state with Test 3's `UTMIP_BIAS_STS0 = 0x00FF01FF` (`PMC_UTMIP_TERM_PAD_CFG = 0x2F8`):**
   - Keep `BIAS_MASTER_PROG_VAL = 1` (`0x7000e670 |= 0x2`), `PD_TRK = 1` (`0x7d00083c = 0x29`), and `FUSE_ATERM_SEL = 1` (`0x7d000834 |= 0x10`) as in Test 2 (`commit 52b3640`), **with** `PMC_UTMIP_TERM_PAD_CFG (0x7000e5f8) = 0x2F8` AND check whether `PMC_SLEEP_CFG (0x7000e5fc)` needs `UTMIP_RCTRL_USE_PMC_P0 | UTMIP_TCTRL_USE_PMC_P0` (`bits [3:2] = 0xc`, i.e., `0xcc` in low byte of `0x7000e5fc`, see `nv_xhci_tegra.c` line 3168!).
2. **Check `XUSB_PADCTL_USB2_PORT_CAP_0` (`0x7009f008`) and `XUSB_PADCTL_OTG_PAD0_CTL1` (`0x7009f098 = 0x23`):**
   - Inspect `/tmp/tegra_usb_pad_ctrl.c` for how `XUSB_PADCTL` initializes and deinitializes UTMI Pad 0 (`0x7009f008`, `0x7009f090`, `0x7009f098`, `0x7009f0a0`).
3. **EHCI Controller Reset (`USBCMD` `0x7d000130` / `HOSTPC1_DEVLC` `0x7d0001b4` / `PORTSC1` `0x7d000174`):**
   - Look at `usb_phy_bringup_host_controller()` in `/tmp/tegra11x_usb_phy.c` lines 502–575: after PHY/pad handoff, how does NVIDIA's driver bring up the EHCI host controller without unbinding the Linux platform driver?
4. **Alternative Zero-Risk Workarounds Available Right Now:**
   - **Option A (MicroSD Card for Payload):** The Surface 2 MicroSD slot uses `sdhci@78000400` (`/dev/mmcblk1p1`), which works 100% natively in Linux `5.17` without touching USB. Keeping `boot.efi` + lean `initrd.gz` (43 MB) on the USB stick (so UEFI boots from USB in 2 seconds) and placing `surface2-rootfs-golden.tar.gz` on a MicroSD card (`/dev/mmcblk1p1`) will be automatically found by the lean `initrd.gz` scanner and install/update eMMC in ~2 minutes.
   - **Option B (Standalone Monolithic `initrd.gz`):** `/home/dxm/surface2-revival/scripts/build_standalone_initrd.py` builds the self-contained monolithic `initrd.gz` where UEFI loads the entire payload into RAM before Linux boots.
