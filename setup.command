#!/bin/bash

# Ensure the terminal is working out of the correct directory
cd "$(dirname "$0")"

echo "========================================="
echo "        TomeBox Automated Installer      "
echo "========================================="
echo ""

# Mac/Linux often use 'python3' instead of 'python'
if command -v python3 &>/dev/null; then
    python3 install.py
elif command -v python &>/dev/null; then
    python install.py
else
    echo "[ERROR] Python is not installed!"
    echo "Please download and install Python from https://www.python.org/downloads/"
fi

echo ""
echo "Setup script has finished."
read -p "Press Enter to close this window..."