@echo off
title Surface 2 - Enable Test Signing

echo ===================================================
echo   Microsoft Surface 2 (RT) - Enable Test Signing
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

echo [*] Enabling test-signing in BCD boot manager...
bcdedit /set {bootmgr} testsigning on
if errorlevel 1 (
    echo [ERROR] Failed to set testsigning on {bootmgr}
    pause
    exit /b 1
)

bcdedit /set {default} testsigning on
if errorlevel 1 (
    echo [ERROR] Failed to set testsigning on {default}
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   [SUCCESS] Test Signing has been enabled!
echo ================================================================
echo.
echo You can now run unsigned / test-signed ARM32 applications.
echo.
echo After your next restart, you will see a 'Test Mode' watermark
echo in the bottom-right corner of the desktop.
echo.
pause
