"""Windows WireGuard backend.

Kernel driver (WireGuardNT) via the bundled official MSI. Tunnel runs as an
auto-start Windows service. Elevation via UAC (ShellExecute "runas").
"""

import ctypes
import glob
import os
import subprocess
import time
from typing import Optional

from core import wireguard as wg

WIREGUARD_DIR = r"C:\Program Files\WireGuard"
WG_EXE = os.path.join(WIREGUARD_DIR, "wg.exe")
WIREGUARD_EXE = os.path.join(WIREGUARD_DIR, "wireguard.exe")
SERVICE_NAME = f"WireGuardTunnel${wg.TUNNEL_NAME}"

CREATE_NO_WINDOW = 0x08000000   # keeps every subprocess from flashing a console


def _run(args, timeout=30):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


class Backend:
    name = "windows"
    no_window_flag = CREATE_NO_WINDOW

    def traceroute_cmd(self, host: str, max_hops: int) -> list:
        # -d = don't resolve names (faster), -h = max hops, -w = per-hop timeout ms
        return ["tracert", "-d", "-h", str(max_hops), "-w", "1500", host]
    
    def tools_available(self) -> bool:
        return os.path.exists(WG_EXE) and os.path.exists(WIREGUARD_EXE)

    def can_auto_install(self) -> bool:
        return _find_msi() is not None

    def install_hint(self) -> str:
        if _find_msi() is None:
            return ("WireGuard couldn't be installed automatically (no bundled "
                    "installer for this system). Install it from wireguard.com/install.")
        return "WireGuard will be installed automatically."

    # -- elevated --------------------------------------------------------------

    def install_tools(self) -> tuple[bool, str]:
        if self.tools_available():
            return True, "already installed"
        msi = _find_msi()
        if not msi:
            return False, "No bundled WireGuard installer for this system."
        try:
            r = _run(["msiexec", "/i", msi, "/qn", "/norestart", "DO_NOT_LAUNCH=1"],
                     timeout=300)
            if r.returncode not in (0, 3010):   # 3010 = ok, wants reboot
                return False, f"WireGuard install failed (msiexec {r.returncode})"
        except Exception as e:
            return False, f"WireGuard install failed: {e}"
        for _ in range(30):
            if self.tools_available():
                return True, "installed"
            time.sleep(1)
        return False, "WireGuard installed but binaries not found — a reboot may be needed."

    def install_tunnel(self, conf_path: str) -> tuple[bool, str]:
        try:
            r = _run([WIREGUARD_EXE, "/installtunnelservice", conf_path])
            if r.returncode != 0:
                return False, f"Tunnel install failed: {r.stderr.strip() or 'unknown'}"
            return True, "installed"
        except Exception as e:
            return False, f"Tunnel install failed: {e}"

    def uninstall_tunnel(self) -> bool:
        try:
            _run([WIREGUARD_EXE, "/uninstalltunnelservice", wg.TUNNEL_NAME])
            time.sleep(2)
            return True
        except Exception:
            return False

    def open_firewall(self, app_port: int) -> None:
        # netsh is a plain console app (no PowerShell window). Idempotent-ish:
        # delete-then-add so repeated setups don't stack duplicate rules.
        for proto, port, label in (
            ("TCP", app_port, "TomeBox (WireGuard)"),
            ("UDP", wg.LISTEN_PORT, "WireGuard (TomeBox)"),
        ):
            _run(["netsh", "advfirewall", "firewall", "delete", "rule",
                  f"name={label}"], timeout=20)
            _run(["netsh", "advfirewall", "firewall", "add", "rule",
                  f"name={label}", "dir=in", "action=allow",
                  f"protocol={proto}", f"localport={port}"], timeout=20)

    # -- unprivileged ----------------------------------------------------------

    def elevate(self, argv: list[str], work_dir: str) -> tuple[bool, str]:
        # Use pythonw.exe (no console) when running from source.
        exe = argv[0]
        if exe.lower().endswith("python.exe"):
            candidate = exe[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(candidate):
                exe = candidate
        params = " ".join(f'"{a}"' if " " in a else a for a in argv[1:])
        try:
            # SW_HIDE (0) so no window flashes.
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, work_dir, 0)
            if rc <= 32:
                if rc == 5:
                    return False, "Administrator permission was declined."
                return False, f"Couldn't start setup (ShellExecute {rc})."
            return True, "elevated"
        except Exception as e:
            return False, f"Couldn't start setup: {e}"


def _find_msi() -> Optional[str]:
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE", "")).upper()
    arch = {"AMD64": "amd64", "ARM64": "arm64", "X86": "x86"}.get(arch, "amd64")
    matches = sorted(glob.glob(wg.resource_path("resources", f"wireguard-{arch}-*.msi")))
    return matches[-1] if matches else None
