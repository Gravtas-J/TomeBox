@echo off
title TomeBox Setup
echo =========================================
echo         TomeBox Automated Installer      
echo =========================================
echo.

:: 1. Check if Python is already installed and accessible
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Python is already installed.
    goto :run_install
)

:: 2. Python not found. Automate the download and installation.
echo [INFO] Python not found on system PATH. 
echo [INFO] Downloading Python 3.11... (This may take a minute)
echo.

set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
set PYTHON_EXE=python_installer.exe

curl -L -o "%PYTHON_EXE%" "%PYTHON_URL%"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Failed to download Python. Please check your internet connection.
    pause
    exit /b
)

echo [INFO] Installing Python silently...
:: /quiet skips the UI wizard. PrependPath=1 ensures it works normally on next reboot.
start /wait "" "%PYTHON_EXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0

echo [INFO] Cleaning up installer...
del "%PYTHON_EXE%"

:: Define where the silent installer puts Python by default
set LOCAL_PYTHON="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if exist %LOCAL_PYTHON% (
    echo [INFO] Python installed successfully.
    echo.
    :: CRITICAL: Call the absolute path, not just 'python'
    %LOCAL_PYTHON% install.py
    pause
    exit /b
) else (
    echo [ERROR] Python installation failed or installed to an unexpected directory.
    pause
    exit /b
)

:run_install
echo [INFO] Running installer...
python install.py
pause