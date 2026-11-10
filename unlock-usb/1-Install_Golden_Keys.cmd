@echo off
setlocal EnableDelayedExpansion
title Surface 2 - Golden Keys Installer

echo ===================================================
echo   Microsoft Surface 2 (RT) - Golden Keys Installer
echo ===================================================
echo.

:: 1. Check for Administrative Privileges via fsutil (works reliably on Windows RT)
fsutil dirty query %systemdrive% >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Elevated administrator rights may be missing.
    echo If this script fails, ensure you right-clicked and selected 'Run as administrator'.
    echo.
)

:: 2. Ensure we are in the correct directory (crucial for USB / elevated cmd)
cd /d "%~dp0"
echo [*] Working directory: %CD%

:: 3. Verify that source files exist
if not exist "%~dp0SecureBootDebug.efi" (
    echo [ERROR] Required file missing: %~dp0SecureBootDebug.efi
    echo Make sure all files from the package are in the same folder.
    echo.
    pause
    exit /b 1
)
if not exist "%~dp0SecureBootDebugPolicy.p7b" (
    echo [ERROR] Required file missing: %~dp0SecureBootDebugPolicy.p7b
    echo Make sure all files from the package are in the same folder.
    echo.
    pause
    exit /b 1
)
echo [*] Source files verified.

:: 4. Disable BitLocker protectors
echo [*] Disabling BitLocker protectors on system drive...
manage-bde -protectors %systemdrive% -disable >nul 2>&1

:: 5. Mount EFI System Partition to S:
echo [*] Mounting EFI System Partition to S:...
mountvol S: /s
if not exist S:\EFI (
    echo [ERROR] Failed to mount EFI partition to S:!
    echo Ensure you right-clicked and ran this script as Administrator.
    pause
    exit /b 1
)

:: 6. Copy files to EFI partition
echo [*] Copying SecureBootDebug.efi to S:\EFI\Microsoft\Boot\...
copy /Y "%~dp0SecureBootDebug.efi" S:\EFI\Microsoft\Boot\SecureBootDebug.efi >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy SecureBootDebug.efi to S:\EFI\Microsoft\Boot\
    mountvol S: /d
    pause
    exit /b 1
)

echo [*] Copying SecureBootDebugPolicy.p7b to S:\...
copy /Y "%~dp0SecureBootDebugPolicy.p7b" S:\SecureBootDebugPolicy.p7b >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy SecureBootDebugPolicy.p7b to S:\
    mountvol S: /d
    pause
    exit /b 1
)

:: 7. Verify destination file physically exists
if not exist "S:\EFI\Microsoft\Boot\SecureBootDebug.efi" (
    echo [ERROR] Verification failed! SecureBootDebug.efi was not found on S:
    mountvol S: /d
    pause
    exit /b 1
)
echo [OK] Verified SecureBootDebug.efi is present on S:\EFI\Microsoft\Boot\

:: 8. Register BCD Boot entry
echo [*] Configuring BCD boot manager...
set var={9809d174-88ef-11e1-8346-00155de8c610}
bcdedit /create "%var%" /d "KitsPolicyTool" /application osloader >nul 2>&1
bcdedit /set "%var%" path "\EFI\Microsoft\Boot\SecureBootDebug.efi" >nul
bcdedit /set "%var%" loadoptions Install >nul
bcdedit /set "%var%" device partition=S: >nul
bcdedit /set {bootmgr} bootsequence "%var%" >nul
if errorlevel 1 (
    echo [ERROR] Failed to set BCD bootsequence!
    mountvol S: /d
    pause
    exit /b 1
)

:: 9. Unmount S:
echo [*] Unmounting EFI System Partition...
mountvol S: /d

echo.
echo ================================================================
echo   [SUCCESS] Golden Keys policy staged and verified!
echo ================================================================
echo.
echo NEXT STEPS ON REBOOT:
echo  1. The Surface will restart into the:
echo        'Secure Boot Debug Policy Applicator'
echo  2. Use the physical Volume Down button to highlight:
echo        'Accept and Install'
echo  3. Press the Windows logo touch button below the screen to confirm.
echo  4. The tablet will finish booting back to Windows RT.
echo.
set /p CONFIRM="Press [ENTER] to restart now, or close this window to restart later..."
shutdown /r /t 0
