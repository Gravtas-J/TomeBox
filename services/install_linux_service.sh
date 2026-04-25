#!/bin/bash
set -e

# Must run as root
if [ "$EUID" -ne 0 ]; then
    echo "[ERROR] This script must be run as root (use sudo)."
    exit 1
fi

INSTALL_DIR="/opt/tomebox"
SERVICE_FILE="/etc/systemd/system/tomebox.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

echo "==============================================="
echo "  TomeBox Linux Service Installer"
echo "==============================================="
echo ""

# Create service user if missing
if ! id -u tomebox >/dev/null 2>&1; then
    echo "[INFO] Creating 'tomebox' service user..."
    useradd --system --no-create-home --shell /usr/sbin/nologin tomebox
fi

# Copy files to /opt/tomebox
echo "[INFO] Installing TomeBox to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r "$SOURCE_DIR"/* "$INSTALL_DIR/"
chown -R tomebox:tomebox "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/TomeBox"

# Install systemd unit
echo "[INFO] Installing systemd service..."
cp "$SCRIPT_DIR/tomebox.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable tomebox.service

echo ""
echo "[INFO] TomeBox service installed successfully!"
echo ""
echo "Start the service:    sudo systemctl start tomebox"
echo "Stop the service:     sudo systemctl stop tomebox"
echo "View logs:            sudo journalctl -u tomebox -f"
echo "Service status:       sudo systemctl status tomebox"
echo ""
echo "The service will start automatically on boot."
echo ""