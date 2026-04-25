@echo off
title TomeBox Service Installer
echo =========================================
echo    TomeBox Windows Service Installer
echo =========================================
echo.

:: Check for admin rights
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script requires administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

:: Check for NSSM
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] NSSM not found in PATH.
    echo.
    echo Please download NSSM from https://nssm.cc/download
    echo Extract nssm.exe to a folder in your PATH (e.g., C:\Windows\System32)
    echo Then re-run this installer.
    echo.
    pause
    exit /b 1
)

:: Get the absolute path to TomeBox.exe (one level up from this script)
set "SCRIPT_DIR=%~dp0"
set "TOMEBOX_EXE=%SCRIPT_DIR%..\TomeBox.exe"

if not exist "%TOMEBOX_EXE%" (
    echo [ERROR] TomeBox.exe not found at: %TOMEBOX_EXE%
    echo Make sure this script is in the 'service' folder next to TomeBox.exe.
    pause
    exit /b 1
)

echo [INFO] Installing TomeBox as a Windows service...
echo.

:: Install the service
nssm install TomeBox "%TOMEBOX_EXE%" --headless --host 0.0.0.0 --port 8000

:: Configure service properties
nssm set TomeBox DisplayName "TomeBox Audiobook Server"
nssm set TomeBox Description "Self-hosted audiobook manager and streaming server"
nssm set TomeBox Start SERVICE_AUTO_START
nssm set TomeBox AppDirectory "%SCRIPT_DIR%.."

:: Configure logging
nssm set TomeBox AppStdout "%SCRIPT_DIR%..\logs\service_stdout.log"
nssm set TomeBox AppStderr "%SCRIPT_DIR%..\logs\service_stderr.log"
nssm set TomeBox AppRotateFiles 1
nssm set TomeBox AppRotateBytes 5242880

:: Restart on crash
nssm set TomeBox AppExit Default Restart
nssm set TomeBox AppRestartDelay 10000

echo.
echo [INFO] Service installed successfully!
echo.
echo To start the service now:    nssm start TomeBox
echo To stop the service:         nssm stop TomeBox
echo To uninstall the service:    nssm remove TomeBox confirm
echo.
echo Or use the Windows Services panel (services.msc) to manage it.
echo.
pause