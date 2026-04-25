@echo off
title TomeBox Service Uninstaller

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script requires administrator privileges.
    pause
    exit /b 1
)

echo Stopping TomeBox service...
nssm stop TomeBox 2>nul

echo Removing TomeBox service...
nssm remove TomeBox confirm

echo Done.
pause