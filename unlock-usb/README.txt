========================================================================
Microsoft Surface 2 (Model 1572) Secure Boot Jailbreak Toolkit
========================================================================

Requirements:
- Microsoft Surface 2 (Nvidia Tegra 4)
- Windows RT 8.1 with updates prior to October 2016
- Keep Wi-Fi DISCONNECTED during this process

------------------------------------------------------------------------
STEP 1: Install Golden Keys Debug Policy
------------------------------------------------------------------------
1. Right-click '1-Install_Golden_Keys.cmd' and select 'Run as administrator'.
2. The script will copy the signed debug policy files to the EFI partition
   and set the one-time boot sequence.
3. Press Enter when prompted to restart the tablet.
4. During reboot, the screen will display:
      'Secure Boot Debug Policy Applicator'
   - Use the hardware Volume Down rocker to highlight: 'Accept and Install'
   - Press the Windows logo touch button on the bottom bezel to confirm.
5. The tablet will reboot and load Windows RT 8.1 normally.

------------------------------------------------------------------------
STEP 2: Enable Test Signing
------------------------------------------------------------------------
1. Once back in Windows RT, right-click '2-Enable_Test_Signing.cmd'
   and select 'Run as administrator'.
2. This configures the BCD to permit running test-signed binaries.
3. (Optional) Restart the device to verify the 'Test Mode' watermark
   in the bottom-right corner of the desktop.

------------------------------------------------------------------------
STEP 3: Permanently Unlock Secure Boot (Yahallo)
------------------------------------------------------------------------
1. Right-click '3-Disable_Secure_Boot_Yahallo.cmd' and select 'Run as administrator'.
2. The script stages Yahallo.efi to the EFI partition and registers it in BCD.
3. Press Enter to restart.
4. Yahallo will execute during boot and permanently disable Secure Boot
   in the Tegra 4 TrustZone NVRAM.
5. You can now chainload custom UEFI bootloaders, GRUB, and Linux!
