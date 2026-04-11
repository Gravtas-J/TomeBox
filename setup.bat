@echo off
title TomeBox Setup
echo =========================================
echo         TomeBox Automated Installer      
echo =========================================
echo.

:: Check if Python is installed and accessible
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your system PATH!
    echo.
    echo 1. Go to https://www.python.org/downloads/
    echo 2. Download the latest installer for Windows.
    echo 3. IMPORTANT: When you run the installer, look at the bottom
    echo    of the very first screen and CHECK THE BOX that says:
    echo    "Add Python to PATH" before clicking Install.
    echo.
    echo Once Python is installed, double-click this setup.bat file again.
    echo.
    pause
    exit /b
)

:: Run the Python installer
python install.py

echo.
echo Setup script has finished.
pause