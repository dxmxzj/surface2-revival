# Microsoft Surface 2 (Tegra 4) — Ubuntu 22.04 Internal eMMC & Smart Display Runbook

> [!IMPORTANT]
> This document captures every hardware quirk, Device Tree (DTB) fix, `systemd` service fix, and boot configuration required to convert a stock **Microsoft Surface 2 (Model 1572, NVIDIA Tegra 4 T114, 32-bit ARMv7, 2 GB RAM, 32 GB eMMC)** from Windows RT 8.1 into a standalone **Ubuntu 22.04 LTS Smart Display Appliance** booting directly from internal eMMC (`/dev/mmcblk0`) with zero external USB or MicroSD media.

---

## 1. Architecture & Partition Layout (`/dev/mmcblk0`)

The internal 32 GB eMMC (`/dev/mmcblk0`) uses an MBR (`msdos`) partition table with two partitions:

| Partition | Size | Filesystem | Mount Point | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `/dev/mmcblk0p1` | `128 MB` | `FAT32` (`vfat`) | `/boot/efi` | UEFI Boot Partition (`bootarm.efi`, `bootmgfw.efi`, `bcd`, `boot.efi`, `cmdline.txt`, `tegra114-microsoft-surface-2.dtb`) |
| `/dev/mmcblk0p2` | `~29.0 GB` | `ext4` | `/` | Ubuntu 22.04 LTS (`jammy`) ARMhf root filesystem + Smart Display Appliance stack |

### Boot Chain Flow
```mermaid
flowchart LR
    A["Power Button"] --> B["Surface 2 UEFI (Secure Boot Disabled via Yahallo)"]
    B --> C["/dev/mmcblk0p1: efi/microsoft/boot/bootmgfw.efi (test.efi)"]
    C --> D["/dev/mmcblk0p1: boot.efi (Linux 5.17.0-rc3 ARMhf + Tegra VDE)"]
    D --> E["/dev/mmcblk0p1: cmdline.txt + tegra114-microsoft-surface-2.dtb"]
    E --> F["/dev/mmcblk0p2: Ubuntu 22.04 systemd + surface-wifi.service + surface-dashboard.service"]
```

---

## 2. Critical Hardware & Device Tree (DTB) Fixes Discovered

We encountered and solved five major hardware/kernel pitfalls on the Surface 2 (`Tegra 114`):

### Fix 1: Keeping the 1080p Internal DSI Panel (`samsung,106hl02`) On
- **Symptom:** Booting the `5.17.0-rc3-next-20220207` (`7,508,480`-byte `boot.efi`) kernel with the `2024-09-30` Open-RT DTB (`22,542` bytes) caused the screen to turn black ~1 second into kernel boot (`[0.98s] drm: tegradrmfb`).
- **Root Cause:**
  1. The `2024-09-30` DTB changed `/host1x@50000000/dsi@54300000/panel@0:reset-gpios` from `<0x05 0x56 0x00>` (`GPIO_ACTIVE_HIGH` on GPIO 86) to `<0x05 0x6c 0x01>` (`GPIO_ACTIVE_LOW` on GPIO 108). Because `panel-samsung-106hl02` in `boot.efi` calls `gpiod_set_value_cansleep(reset_gpio, 1)` assuming active-high logic, marking GPIO 108 `GPIO_ACTIVE_LOW` (`0x01`) inverted the signal to physical `0` (LOW), holding the LCD panel in hardware reset!
  2. The `2024-09-30` DTB also removed `regulator-always-on; regulator-boot-on;` from `/i2c@7000d000/pmic@58/pmic/regulators/ldoln` (the 1.8V MIPI DSI PLL supply) and changed `/backlight:pwms` period from `100000` ns (10 kHz) to `1000000` ns (1 kHz).
- **Solution:** Start from the `2024-04` eMMC DTB (`dtb-21906.dtb`, which keeps `panel@0:reset-gpios = <0x05 0x56 0x00>`, `/backlight:pwms = <0x1a 0 100000>`, and `ldoln` always-on), which keeps the 1080p display and Type Cover working reliably.

### Fix 2: Eliminating `irq 96: nobody cared` / `Disabling IRQ #96`
- **Symptom:** ~23 seconds into boot, the kernel hung for several seconds and printed `irq 96: nobody cared ... Disabling IRQ #96`.
- **Root Cause:** `/proc/interrupts` revealed `96: 100001 GPIO 146 Level tps65090`. The `pmic@48` (`ti,tps65090`) node under `/i2c@7000d000` had `interrupt-parent = <0x05>` and `interrupts = <0x92 0x04>` (GPIO 146 active-low level interrupt) plus a `charger` subnode whose unacknowledged charger status bit held GPIO 146 asserted continuously.
- **Solution:** Remove `interrupt-parent`, `interrupts`, and the `charger` child node from `/i2c@7000d000/pmic@48`. All `tps65090` power rails (`dcdc1`–`dcdc3`, `fet1`, `fet4`, `fet6`, `ldo1`, `ldo2`) continue operating normally via I2C without the IRQ storm.

### Fix 3: Preventing Unused Regulator Shutdown (`vdd-ao`, `rail-3v3`, `rail-5v0`, `usb2_vbus`, `what_ever`)
- **Symptom:** ~30–60 seconds after kernel boot, `regulator_init_complete_work` disables any PMIC regulator not claimed by a driver or marked `regulator-always-on`, cutting power to the Marvell Wi-Fi rail or USB VBUS.
- **Solution:** Add `regulator-always-on` and `regulator-boot-on` to:
  - `/i2c@7000d000/pmic@48/regulators/dcdc3` (`vdd-ao`)
  - `/i2c@7000d000/pmic@58/pmic/regulators/regen1` (`rail-3v3`)
  - `/i2c@7000d000/pmic@58/pmic/regulators/regen2` (`rail-5v0`)
  - `/regulator-usb3` (`usb2_vbus`)
  - `/what_ever` (`GPIO 0x3a` Wi-Fi rail)

### Fix 4: Powering the Atmel maXTouch Touchscreen (`03EB:843C`) and Restoring Touch Input
- **Symptom:** The Atmel capacitive touchscreen controller (`03EB:843C` on I2C bus 1 at address `0x4b`) was completely inactive, did not appear on I2C or input events, and IRQ 102 never fired.
- **Root Cause & ACPI Extraction:**
  - Dumped Microsoft Surface 2 official ACPI tables (`DSDT` and `SSDT` from physical memory `0xfdf1c000`).
  - Table `AP20EDK2` describes device `\_SB.TOUC` (`_HID = "MSHW0100"`, `_CID = "PNP0C50"`):
    1. **Power Resource (`PRTU`):** Controls `ESD1` on `TEGRA_GPIO(K, 6)` = **GPIO 86** (`0x56`). `PRTU._ON` drives GPIO 86 HIGH (`1`).
    2. **Reset Resource (`PRGR`):** Controls `GSRT` on `TEGRA_GPIO(H, 2)` = **GPIO 58** (`0x3a`). `PRGR._ON` pulses GPIO 58 LOW for 100ms, then drives it HIGH (`1`).
    3. **Interrupt Line:** `TEGRA_GPIO(O, 6)` = **GPIO 118** (GIC IRQ 102), Active Low Level.
  - In the original baseline DTB (`dtb-21906.dtb`), GPIO 86 was mistakenly assigned to `panel@0` as `reset-gpios = <0x05 0x56 0x00>`. As a result, the panel driver held GPIO 86 at 0V, cutting power to the touchscreen controller.
- **Solution:**
  1. Created [`surface-touch-init.service`](file:///home/dxm/surface2-revival/systemd/surface-touch-init.service) and [`surface-touch-init.py`](file:///home/dxm/surface2-revival/appliance/surface-touch-init.py), which runs at boot before graphical services to configure the Tegra GPIO controller (`0x6000d000`), drive GPIO 86 HIGH, and pulse GPIO 58 LOW $\rightarrow$ HIGH matching Microsoft's `PRGR` specification.
  2. The controller immediately responds on I2C bus 1 at `0x4b`, registers as `/dev/input/event2` (4096 $\times$ 4096 `ABS_X`/`ABS_Y` and `BTN_TOUCH`), and services IRQ 102.
  3. **Touch Coordinate Synchronization (`EV_SYN`):** The Linux input subsystem delivers `BTN_TOUCH=1` (press) *before* the new packet's `ABS_X` and `ABS_Y` coordinates. Both [`surface-touch-kbd.py`](file:///home/dxm/surface2-revival/appliance/surface-touch-kbd.py) and [`surface2_smart_receiver.py`](file:///home/dxm/surface2-revival/appliance/surface2_smart_receiver.py) wait for `EV_SYN / SYN_REPORT` before evaluating coordinates, completely eliminating "stale key buffering" (e.g. typing "da" producing "dd").
  4. **Clean Handoff:** Transitions between Dashboard and Terminal use synchronous `systemctl` calls and zero the top 608px framebuffer buffer, preventing ghost dashboard artifacts from leaking into the console.

### Fix 5: `systemd` Default `wpa_supplicant.service` Conflict & `Type=forking` 90s Timeout
- **Symptoms:**
  1. On first eMMC `systemd` boot, `mwifiex_sdio` loaded (`mlan0`), but `wpa_supplicant` failed to associate to Wi-Fi.
  2. When `surface-wifi.service` was configured as `Type=forking`, `systemd` killed `wpa_supplicant` and `dhclient` exactly **90 seconds** after boot (`TimeoutStartSec=90s`) because the background keepalive subshell inherited open file descriptors from `surface-online.sh`.
- **Solutions:**
  1. Mask the default unconfigured Ubuntu `wpa_supplicant.service`:
     ```bash
     rm -f /etc/systemd/system/multi-user.target.wants/wpa_supplicant.service
     rm -f /etc/systemd/system/dbus-fi.w1.wpa_supplicant1.service
     ln -sf /dev/null /etc/systemd/system/wpa_supplicant.service
     ```
  2. Configure `/etc/systemd/system/surface-wifi.service` with `Type=simple`, `Restart=always`, and `KillMode=process`, and keep the `while true` watchdog loop in the foreground of `/usr/local/bin/surface-online.sh` (plus disable Marvell Wi-Fi power save via `iw dev mlan0 set power_save off`).

### Fix 6: Fast Boot & Continuous Visible Boot Text (`cmdline.txt`)
- **Symptom:** Booting from USB showed `"using DTB from command line"` for ~45 seconds (while UEFI loaded the 43 MB `initrd.gz` over USB 2.0), followed by a quiet screen.
- **Solution:**
  - Booting directly from `/dev/mmcblk0p1` into `/dev/mmcblk0p2` uses **no `initrd.gz`**, so `"using DTB from command line"` finishes in **< 1 second**.
  - Set `/boot/efi/cmdline.txt` to:
    ```text
    dtb=tegra114-microsoft-surface-2.dtb root=/dev/mmcblk0p2 console=tty1 consoleblank=0 loglevel=7 systemd.show_status=true cpuidle.off=1 rootwait rw
    ```
  - Configure `/etc/issue` to display the live `mlan0` IPv4 address (`\4{mlan0}`) and login instructions right on `tty1`.

---

## 3. Step-by-Step Procedure for a New Surface 2

### Phase 1: Unlock Secure Boot in Windows RT 8.1 (One-Time per Device)
> [!IMPORTANT]
> To prevent Windows Boot Manager from attempting to load unsigned Linux boot files (`\test.efi`) from the USB drive before Secure Boot is disabled, always run the unlock scripts from the local Windows RT disk:

1. Boot Surface 2 into Windows RT 8.1 and insert the USB drive.
2. Copy the `Unlock-SecureBoot` folder from the USB drive to the Windows RT Desktop (or `C:\Unlock-SecureBoot`), then **unplug the USB drive**.
3. In the Desktop `Unlock-SecureBoot` folder, run each as Administrator:
   - `1-Install_Golden_Keys.cmd` (installs Golden Keys debug policy and registers BCD entry; reboot when prompted).
     - On reboot, the screen displays "Secure Boot Debug Policy Applicator". Press physical **Volume Down** to select *Accept and Install*, then press the capacitive **Windows logo** touch button to confirm. Tablet boots back to Windows RT.
   - `2-Enable_Test_Signing.cmd` (enables test signing in BCD; note that this script exits back to command prompt and does not automatically reboot).
   - `3-Disable_Secure_Boot_Yahallo.cmd` (triggers `Yahallo.efi` on next reboot; reboot when prompted).
4. On the blue UEFI prompt, press physical **Volume Down** to select **Disable Secure Boot**.
   Secure Boot is now permanently disabled!

### Phase 2: Boot Unattended Installer (Zero Typing / Standalone All-In-One USB)
> [!NOTE]
> Because Tegra 4 Linux kernel transceivers suspend standard Type-A USB storage devices after handoff, the installer embeds the complete golden boot and rootfs payloads directly inside the ramdisk (`initrd.gz`). UEFI loads the entire 435 MB environment into physical RAM over USB in ~25 seconds, eliminating any runtime USB driver or MicroSD dependency!

1. Copy the updated [`initrd.gz`](file:///mnt/d/surface2-usb-files/initrd.gz) and files from `D:\surface2-usb-files` to your FAT32 USB drive (`E:`).
2. Insert the USB drive into the unlocked Surface 2 while powered off.
3. Hold physical **Volume Down**, press and release **Power**, and keep holding **Volume Down** until the Surface logo appears with the loading dots.
4. Release Volume Down.
5. **Completely hands-off:**
   - UEFI loads the kernel and all-in-one ramdisk into RAM (~25 seconds).
   - The installer displays progress on the tablet screen (`/dev/tty1`):
     - `[+] Embedded installer payload located in memory!`
     - `[1/4] Partitioning internal 32 GB eMMC (/dev/mmcblk0)...`
     - `[2/4] Formatting EFI and Root filesystems...`
     - `[3/4] Extracting Ubuntu 22.04 LTS Smart Display Rootfs (takes ~90s)...`
     - `[4/4] Installing UEFI Boot Chain & Golden DTB...`
   - Wi-Fi and Dropbear SSH are also brought up in the background automatically.
6. When complete, the tablet displays a 15-second countdown on screen:
   - Unplug the USB drive during the countdown.
   - The tablet automatically reboots directly from internal eMMC into Ubuntu 22.04 LTS!
5. **Authorize Tailscale on the New Tablet:**
   - Once booted into Ubuntu 22.04 over Wi-Fi, SSH in and run:
     ```bash
     tailscale up --hostname=surface2-2 --accept-routes
     ```
   - Note: Because the `5.17.0-rc3-next-20220207` kernel does not include `CONFIG_TUN`, `/etc/default/tailscaled` is pre-configured with `FLAGS="--tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055"`. Incoming connections to SSH (`22`), Web Controller (`8080`), and AirPlay (`7000`) work seamlessly over the Tailscale IP.

---

## 4. Smart Display Appliance, Dual-Head `YU12` Video Engine & Touch Keyboard

Once booted into Ubuntu 22.04 from internal eMMC, the Surface 2 runs the Smart Display Appliance stack:
- **Dual-Display 1080p Smart Display Dashboard (`surface-dashboard.service`):** Renders live clock, system metrics, battery status, Wi-Fi/Tailscale IP, and streaming instructions directly to `/dev/fb0` (`1920x1080` 32bpp `BGRA`), mirroring simultaneously on both the internal 1080p LCD (`DSI-1`) and 1080p60 Micro-HDMI (`HDMI-A-1`).
- **Dual-Head Hardware `YU12` (`YUV420P`) Overlay Video Engine (`DualOverlayStreamPlayer`):**
  - Uses Tegra 4's dedicated hardware planar `YU12` (`YUV420P`) overlay planes — **`Plane 42` on `CRTC 64` (Micro-HDMI)** and **`Plane 76` on `CRTC 98` (Internal 1080p LCD)** — which perform `YUV -> RGB` color space conversion and scaling in dedicated display silicon at **0% CPU**.
  - Binds both hardware overlay planes once at stream start to a shared CMA scanout buffer (`1920x1080` `YU12`) so both Display Controllers (`DC0` and `DC1`) scan out the video continuously at 60 Hz with **zero per-frame VBLANK blocking**, achieving full 30 fps 1080p RTSP streaming with ~59% idle CPU.
- **Apple AirPlay Receiver (`uxplay.service`):** Advertises `Surface2` on the LAN for Mac/iPhone/iPad screen mirroring; automatically yields the display when an AirPlay client connects and restores the dashboard on disconnect.
- **Web Controller & RTSP Cast API (`http://<surface2-ip>:8080`):** Cast any RTSP/HTTP video stream via browser or `curl "http://<ip>:8080/play?url=rtsp://..."`.

### Dropping to the Terminal (`tty1`), On-Screen Touch Keyboard, and Relaunching the Dashboard
- **To Exit the Dashboard to the Terminal (`tty1`):**
  1. **Touchscreen:** Tap the **`[ Terminal / Shell (tty1) ]`** button in the top-right corner of the 1080p dashboard.
  2. **Keyboard:** Press **`q`** or **`Esc`** on the Surface Type Cover (or GLKVM / USB keyboard).
  3. **Web / SSH:** Click **Drop to Terminal (tty1)** in the Web UI (`http://<ip>:8080/terminal`) or run [`dashboard`](file:///home/dxm/surface2-revival/appliance/dashboard) `stop`.
- **Automatic On-Screen Touch Keyboard (`surface-touch-kbd.service`):**
  - Whenever you drop out of the Dashboard to `tty1`, [`surface-touch-kbd.py`](file:///home/dxm/surface2-revival/appliance/surface-touch-kbd.py) automatically starts at the bottom of the screen (`y = 630..1080`) and resizes `tty1` to 38 rows (`TIOCSWINSZ`) so terminal text never overlaps the keyboard.
  - Reads touch coordinates from the Atmel maXTouch touchscreen (`03EB:843C`, `ABS_X/Y 0..4095`) and injects characters and ANSI sequences directly into `/dev/tty1` via `TIOCSTI`.
  - Includes a top quick-action bar with one-tap buttons: **`[ Launch Dashboard ]`**, **`[ Appliance Status ]`**, **`[ Wi-Fi / IP ]`**, **`[ Ctrl+C ]`**, **`[ Clear ]`**, and **`[ Hide Keyboard ]`** (which collapses the keyboard into a small bottom-right pill when using a physical keyboard).
- **To Start the Dashboard Again from the Terminal:**
  - Tap **`[ Launch Dashboard ]`** on the On-Screen Touch Keyboard, or type **`dashboard`** (or **`start-dashboard`**) at the `root@surface2:~#` prompt and press **Enter**.

````carousel
![Live 1080p60 Surface 2 Smart Display Dashboard captured over Micro-HDMI by GLKVM](/home/dxm/.gemini/antigravity-cli/brain/eb3b74ec-0b8b-4dfd-a73b-b3387101d00f/glkvm_hdmi_snapshot.jpg)
<!-- slide -->
![Interactive tty1 Terminal with the 1080p Surface 2 Framebuffer Touch Keyboard & Quick-Action Bar](/home/dxm/.gemini/antigravity-cli/brain/eb3b74ec-0b8b-4dfd-a73b-b3387101d00f/glkvm_touch_kbd_snapshot.jpg)
````

---

## 5. Micro-HDMI 1080p60 SOR PHY Calibration & Fast Hardware Reboot

### Why Micro-HDMI Needed High-Drive TMDS Calibration
In mainline Linux `5.17` (`drivers/gpu/drm/tegra/hdmi.c`), `tegra114_tmds_config` uses `pclk <= 148500000` (`0x08d9ee20`) for its low-power Entry 1 table (`SOR_LANE_DRIVE_CURRENT = 0x1f1f1f1f`, `SOR_IO_PEAK_CURRENT = 0x00000000`). At `1920x1080@60` (`148.5 MHz`), this drive strength is too weak across the Surface 2 Micro-HDMI connector and adapter, causing HDMI receivers (`lt6911c` on GLKVM) to fail TMDS lock or flash white.

Both `/usr/local/bin/surface-hdmi-phy.py` (called by `surface-online.sh`) and [`surface2_smart_receiver.py`](file:///home/dxm/surface2-revival/appliance/surface2_smart_receiver.py) automatically program NVIDIA's Entry 2 (`>148.5 MHz`) TMDS PHY parameters whenever `card0-HDMI-A-1` is `connected` and `enabled` **and** the Tegra CAR HDMI clock (`0x60006014` bit 19) is active:
- `HDMI_NV_PDISP_SOR_PLL0` (`0x57`): `0x01003f10`
- `HDMI_NV_PDISP_SOR_PLL1` (`0x58`): `0x00300f00`
- `HDMI_NV_PDISP_PE_CURRENT` (`0x99`): `0x00000000`
- `HDMI_NV_PDISP_SOR_LANE_DRIVE_CURRENT` (`0x7e`): `0x303f3f3f` (`0x30` clock lane, `0x3f` data lanes)
- `HDMI_NV_PDISP_SOR_IO_PEAK_CURRENT` (`0xd1`): `0x040f0f0f`

Because it verifies both `/sys/class/drm/card0-HDMI-A-1/status` and the CAR clock/reset bits (`0x60006008` / `0x60006014` bit 19) before accessing `0x54280000`, it is 100% safe whether an HDMI cable is plugged in, hotplugged, or unplugged.

### Fast Hardware Reboot (`fast-reboot` & `RebootWatchdogSec=5s`)
On the Surface 2, `reboot -f` triggers the Palmas PMIC power-off handler, while standard `systemctl reboot` relies on the Tegra hardware watchdog (`tegra-wdt` on `/dev/watchdog0`, which defaults to a 120-second heartbeat). To make reboots fast and autonomous:
- `/etc/systemd/system.conf` sets `RuntimeWatchdogSec=30s` and `RebootWatchdogSec=5s`.
- `/usr/local/bin/fast-reboot` syncs filesystems and sets `WDIOC_SETTIMEOUT = 3` seconds on `/dev/watchdog0`, performing a clean hardware SoC reset in 3 seconds.
