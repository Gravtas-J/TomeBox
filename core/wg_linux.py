"""Linux WireGuard backend.

WireGuard is in the kernel (>=5.6), so there's no driver to install — but wg-quick
and the wg tool come from the distro's wireguard-tools package, which we do NOT
bundle (it's a bash script with distro-specific dependencies). If it's missing we
tell the user the exact install command for their distro. Linux users expect this.

Tunnel runs as a systemd unit (wg-quick@tomebox), auto-start at boot. Elevation via
pkexec (GUI prompt) with a sudo fallback.

UNTESTED by the author — written against documented behaviour.
"""

import os
import shutil
import subprocess
from typing import Optional

from core import wireguard as wg

INSTALLED_CONF = f"/etc/wireguard/{wg.TUNNEL_NAME}.conf"
SYSTEMD_UNIT = f"wg-quick@{wg.TUNNEL_NAME}"


class Backend:
    name = "linux"

    def tools_available(self) -> bool:
        return shutil.which("wg") is not None and shutil.which("wg-quick") is not None

    def can_auto_install(self) -> bool:
        # We won't touch the user's package manager unattended. They install it.
        return False

    def install_hint(self) -> str:
        return ("WireGuard tools aren't installed. Install them with your package "
                "manager, then try again:\n"
                f"  {_distro_install_cmd()}")

    # -- elevated (running as root) -------------------------------------------

    def install_tools(self) -> tuple[bool, str]:
        # Never invoked (can_auto_install is False), but be explicit.
        if self.tools_available():
            return True, "already installed"
        return False, self.install_hint()

    def install_tunnel(self, conf_path: str) -> tuple[bool, str]:
        try:
            os.makedirs("/etc/wireguard", exist_ok=True)
            with open(conf_path) as src, open(INSTALLED_CONF, "w") as dst:
                dst.write(src.read())
            os.chmod(INSTALLED_CONF, 0o600)

            # enable = start now AND on every boot.
            r = subprocess.run(["systemctl", "enable", "--now", SYSTEMD_UNIT],
                               capture_output=True, text=True)
            if r.returncode != 0:
                # Fall back to a direct wg-quick up if systemd isn't the init (rare).
                r2 = subprocess.run(["wg-quick", "up", wg.TUNNEL_NAME],
                                   capture_output=True, text=True)
                if r2.returncode != 0:
                    return False, (f"Tunnel start failed: "
                                   f"{r.stderr.strip() or r2.stderr.strip()}")
            return True, "installed"
        except Exception as e:
            return False, f"Tunnel install failed: {e}"

    def uninstall_tunnel(self) -> bool:
        try:
            subprocess.run(["systemctl", "disable", "--now", SYSTEMD_UNIT],
                           capture_output=True)
            subprocess.run(["wg-quick", "down", wg.TUNNEL_NAME], capture_output=True)
            return True
        except Exception:
            return False

    def open_firewall(self, app_port: int) -> None:
        # Firewall situation is a zoo (ufw/firewalld/nftables/none). Try the common
        # front-ends if present; otherwise do nothing and let the user handle it.
        if shutil.which("ufw"):
            subprocess.run(["ufw", "allow", f"{wg.LISTEN_PORT}/udp"], capture_output=True)
            subprocess.run(["ufw", "allow", f"{app_port}/tcp"], capture_output=True)
        elif shutil.which("firewall-cmd"):
            subprocess.run(["firewall-cmd", "--permanent",
                            f"--add-port={wg.LISTEN_PORT}/udp"], capture_output=True)
            subprocess.run(["firewall-cmd", "--permanent",
                            f"--add-port={app_port}/tcp"], capture_output=True)
            subprocess.run(["firewall-cmd", "--reload"], capture_output=True)
        # else: no recognised firewall — assume open, or the user manages it.

    # -- unprivileged ----------------------------------------------------------

    def elevate(self, argv: list[str], work_dir: str) -> tuple[bool, str]:
        inner = " ".join(_shq(a) for a in argv)
        wrapped = f"cd {_shq(work_dir)} && {inner}"

        if shutil.which("pkexec"):
            launcher = ["pkexec", "bash", "-c", wrapped]
        elif shutil.which("sudo"):
            # -A uses an askpass helper if configured; otherwise this needs a tty.
            launcher = ["sudo", "bash", "-c", wrapped]
        else:
            return False, "Need pkexec or sudo to configure remote access, neither found."

        try:
            r = subprocess.run(launcher, capture_output=True, text=True)
            if r.returncode != 0:
                err = (r.stderr or "").strip()
                if "dismissed" in err.lower() or "cancel" in err.lower():
                    return False, "Administrator permission was declined."
                return False, f"Couldn't start setup: {err or 'permission denied'}"
            return True, "elevated"
        except Exception as e:
            return False, f"Couldn't start setup: {e}"


def _distro_install_cmd() -> str:
    if shutil.which("apt"):
        return "sudo apt install wireguard-tools"
    if shutil.which("dnf"):
        return "sudo dnf install wireguard-tools"
    if shutil.which("pacman"):
        return "sudo pacman -S wireguard-tools"
    if shutil.which("zypper"):
        return "sudo zypper install wireguard-tools"
    return "install the 'wireguard-tools' package"


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
