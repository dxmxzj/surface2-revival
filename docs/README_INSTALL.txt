================================================================================
MICROSOFT SURFACE 2 (TEGRA 4) — STANDALONE UBUNTU 22.04 LTS USB INSTALLER
================================================================================

This USB drive contains:
  1. The Windows RT 8.1 Secure Boot Unlock Toolkit (Golden Keys + Yahallo exploit)
  2. The 100% Unattended Ubuntu 22.04 LTS Smart Display Installer (Zero typing / No keyboard needed)

--------------------------------------------------------------------------------
PART 1: UNLOCK SECURE BOOT (WINDOWS RT 8.1)
--------------------------------------------------------------------------------
NOTE: To prevent Windows Boot Manager from attempting to load unsigned Linux
boot files before Secure Boot is unlocked (which causes '\test.efi digital
signature could not be verified'), perform the unlock steps from the local drive:

1. Boot Surface 2 #2 into Windows RT 8.1.
2. Insert this USB drive.
3. Open File Explorer, go to the USB drive, and copy the "Unlock-SecureBoot"
   folder directly to your Windows RT Desktop (or C:\).
4. UNPLUG the USB drive from the tablet.
5. On the Desktop, open the "Unlock-SecureBoot" folder:

   Step A: Right-click "1-Install_Golden_Keys.cmd" -> "Run as administrator".
           Press Enter to reboot.
           On reboot, you will see "Secure Boot Debug Policy Applicator".
           Press physical VOLUME DOWN to highlight "Accept and Install".
           Press the capacitive Windows logo touch button below the screen to confirm.
           The tablet boots back into Windows RT.

   Step B: Open "Unlock-SecureBoot" folder again.
           Right-click "2-Enable_Test_Signing.cmd" -> "Run as administrator".
           (This enables test signing and returns to command prompt without auto-reboot).

   Step C: Right-click "3-Disable_Secure_Boot_Yahallo.cmd" -> "Run as administrator".
           Press Enter to reboot.
           On reboot, you will see a blue UEFI "Disable Secure Boot" screen.
           Press physical VOLUME DOWN to select "Disable Secure Boot".

Secure Boot is now PERMANENTLY DISABLED!

--------------------------------------------------------------------------------
PART 2: UNATTENDED UBUNTU 22.04 eMMC INSTALLATION (ZERO TYPING / NO KEYBOARD NEEDED)
--------------------------------------------------------------------------------
1. Turn off the Surface 2 completely (press and hold Power for 10 seconds).
2. Insert this USB drive into the Surface 2.
3. Boot from USB:
   - Press and hold physical VOLUME DOWN.
   - Press and release POWER button.
   - Keep holding VOLUME DOWN until the "Surface" logo appears with the loading dots.
   - Release VOLUME DOWN.

4. The tablet boots into the automated installer:
   - NO KEYBOARD REQUIRED.
   - The installer runs completely hands-free and displays progress on screen:
     [1/5] Searching for installer media (USB / MicroSD)...
     [2/5] Partitioning internal 32 GB eMMC (/dev/mmcblk0)...
     [3/5] Formatting EFI and Root filesystems...
     [4/5] Extracting Ubuntu 22.04 LTS Smart Display Rootfs...
     [5/5] Installing UEFI Boot Chain & Golden DTB...
   - Wi-Fi and SSH dropbear are also brought up in the background automatically.

5. When installation is complete (~90 seconds), the screen displays:
   ==========================================================================
    [SUCCESS] Surface 2 Ubuntu 22.04 LTS Installation COMPLETE!
   ==========================================================================
    >>> STEP 1: UNPLUG / REMOVE THE USB DRIVE NOW <<<
    >>> STEP 2: Press [ENTER] to reboot (or hold Power button to turn off) <<<

6. Unplug the USB drive from the tablet.
7. Press [ENTER] (or hold the physical Power button for 5s to turn off, then turn back on).
8. The Surface 2 boots directly from internal eMMC into your new Ubuntu 22.04
   appliance with 1080p DSI+HDMI dual mirror, touchscreen, and on-screen keyboard!
================================================================================
