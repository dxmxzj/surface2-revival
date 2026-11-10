@echo off
setlocal EnableDelayedExpansion
title Surface 2 - Permanently Disable Secure Boot (Yahallo)

echo ===================================================
echo   Microsoft Surface 2 - Disable Secure Boot (Yahallo)
echo ===================================================
echo.

:: Check for Administrative Privileges via fsutil
fsutil dirty query %systemdrive% >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Elevated administrator rights may be missing.
    echo If this script fails, ensure you right-clicked and selected 'Run as administrator'.
    echo.
)

cd /d "%~dp0"

:: 2. Verify Yahallo.efi exists
if not exist "%~dp0Yahallo.efi" (
    echo [ERROR] Yahallo.efi not found in %~dp0
    pause
    exit /b 1
)

:: 3. Mount EFI partition
echo [*] Mounting EFI partition to S:...
mountvol S: /s
if not exist S:\EFI (
    echo [ERROR] Failed to mount EFI partition to S:!
    pause
    exit /b 1
)

:: 4. Copy Yahallo.efi
echo [*] Copying Yahallo.efi to S:\EFI\Microsoft\Boot\...
copy /Y "%~dp0Yahallo.efi" S:\EFI\Microsoft\Boot\Yahallo.efi >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy Yahallo.efi to S:\EFI\Microsoft\Boot\
    mountvol S: /d
    pause
    exit /b 1
)

:: 5. Register BCD entry with nointegritychecks and testsigning
echo [*] Registering Yahallo in BCD with test-signing and no integrity checks...
set var={b2823826-6f8c-4a37-b45b-381a17277888}
bcdedit /create "%var%" /d "Yahallo" /application osloader >nul 2>&1
bcdedit /set "%var%" path "\EFI\Microsoft\Boot\Yahallo.efi" >nul
bcdedit /set "%var%" device partition=S: >nul
bcdedit /set "%var%" nointegritychecks on >nul
bcdedit /set "%var%" testsigning on >nul
bcdedit /set {bootmgr} bootsequence "%var%" >nul
if errorlevel 1 (
    echo [ERROR] Failed to set BCD bootsequence for Yahallo!
    mountvol S: /d
    pause
    exit /b 1
)

mountvol S: /d

echo.
echo ================================================================
echo   [SUCCESS] Yahallo staged with test-signing enabled!
echo ================================================================
echo.
echo When your tablet restarts, Yahallo will execute and permanently
echo disable Secure Boot in TrustZone NVRAM.
echo.
set /p CONFIRM="Press [ENTER] to restart now, or close this window to restart later..."
shutdown /r /t 0
