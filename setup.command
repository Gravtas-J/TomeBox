#!/bin/bash

# Ensure the terminal is working out of the correct directory
cd "$(dirname "$0")"

echo "========================================="
echo "        TomeBox Automated Installer      "
echo "========================================="
echo ""

# Function to execute the actual install.py script
run_install() {
    if command -v python3 &>/dev/null; then
        python3 install.py
    elif command -v python &>/dev/null; then
        python install.py
    else
        echo "[ERROR] Could not start Python even after installation attempt."
    fi
}

# 1. Check if Python is already installed
if command -v python3 &>/dev/null || command -v python &>/dev/null; then
    echo "[INFO] Python is already installed."
    run_install
else
    # 2. Python not found. Automate the download and installation.
    echo "[INFO] Python not found on system."
    
    # Check if the operating system is macOS
    if [ "$(uname)" == "Darwin" ]; then
        echo "[INFO] macOS detected. Downloading Python 3.11... (This may take a minute)"
        echo ""
        
        PKG_URL="https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
        PKG_NAME="python_installer.pkg"
        
        curl -L -o "$PKG_NAME" "$PKG_URL"
        
        if [ ! -f "$PKG_NAME" ]; then
            echo "[ERROR] Failed to download Python. Please check your internet connection."
            exit 1
        fi
        
        echo "[INFO] Installing Python silently..."
        echo "[WARNING] Your Mac needs administrator permission to install Python."
        echo "          Please enter your Mac login password if prompted below."
        echo "          (Note: As you type, the password will remain invisible)."
        
        # 'sudo' is required to install .pkg files via the command line
        sudo installer -pkg "$PKG_NAME" -target /
        
        echo "[INFO] Cleaning up installer..."
        rm "$PKG_NAME"
        
        echo "[INFO] Python installed successfully."
        echo ""
        run_install
    else
        # Fallback for Linux users (Linux package managers vary too wildly to safely automate without asking)
        echo "[ERROR] Automated installation is only supported on macOS and Windows."
        echo "Please install Python 3 using your system's package manager (e.g., 'sudo apt install python3' or 'sudo dnf install python3')."
    fi
fi

echo ""
echo "Setup script has finished."
read -p "Press Enter to close this window..."