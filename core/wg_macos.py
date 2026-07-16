"""macOS WireGuard backend.

No kernel driver needed — macOS has utun built in, and WireGuard runs in userspace
via wireguard-go. We bundle wireguard-go + wg + wg-quick, so nothing is installed.
The tunnel runs as a root LaunchDaemon (auto-start at boot). Elevation via osascript
("with administrator privileges"), which shows the standard macOS auth dialog.

UNTESTED by the author — written against documented behaviour. Likely first snag:
Gatekeeper quarantining the bundled binaries. See _clear_quarantine().
"""

import os
import subprocess
import time
from typing import Optional

from core import wireguard as wg

# Bundled binaries live under resources/macos/. wg-quick expects wg and wireguard-go
# on PATH, so we point it at our bundle dir via WG_QUICK env + a PATH prefix.
BUNDLE_DIR = wg.resource_path("resources", "macos")
WG_QUICK = os.path.join(BUNDLE_DIR, "wg-quick")
WG = os.path.join(BUNDLE_DIR, "wg")

LAUNCH_DAEMON = f"/Library/LaunchDaemons/com.tomebox.wireguard.{wg.TUNNEL_NAME}.plist"
INSTALLED_CONF = f"/usr/local/etc/wireguard/{wg.TUNNEL_NAME}.conf"


def _env():
    e = os.environ.copy()
    e["PATH"] = BUNDLE_DIR + os.pathsep + e.get("PATH", "")
    return e


class Backend:
    name = "macos"
    no_window_flag = 0

    def traceroute_cmd(self, host: str, max_hops: int) -> list:
        # -n = numeric, -m = max hops, -w = wait seconds, -q 1 = one probe per hop
        return ["traceroute", "-n", "-m", str(max_hops), "-w", "2", "-q", "1", host]
    def tools_available(self) -> bool:
        # We ship our own binaries, so "available" means the bundle is present.
        return os.path.exists(WG_QUICK) and os.path.exists(WG)

    def can_auto_install(self) -> bool:
        return self.tools_available()   # nothing to install; just unquarantine

    def install_hint(self) -> str:
        if not self.tools_available():
            return ("WireGuard binaries are missing from this build. This is a "
                    "packaging problem — please report it.")
        return "WireGuard is bundled; no separate install needed."

    # -- elevated (running as root) -------------------------------------------

    def install_tools(self) -> tuple[bool, str]:
        # Nothing to install. Clear Gatekeeper quarantine on the bundled binaries so
        # they can execute, and mark them executable.
        try:
            for b in ("wg", "wg-quick", "wireguard-go"):
                p = os.path.join(BUNDLE_DIR, b)
                if os.path.exists(p):
                    os.chmod(p, 0o755)
                    subprocess.run(["xattr", "-d", "com.apple.quarantine", p],
                                   capture_output=True)
            return True, "ready"
        except Exception as e:
            return False, f"Couldn't prepare bundled binaries: {e}"

    def install_tunnel(self, conf_path: str) -> tuple[bool, str]:
        try:
            os.makedirs(os.path.dirname(INSTALLED_CONF), exist_ok=True)
            # Copy the generated config to a stable root-owned location.
            with open(conf_path) as src, open(INSTALLED_CONF, "w") as dst:
                dst.write(src.read())
            os.chmod(INSTALLED_CONF, 0o600)

            with open(LAUNCH_DAEMON, "w") as fh:
                fh.write(_plist())
            os.chmod(LAUNCH_DAEMON, 0o644)

            # Load + start now (and it auto-starts on every boot from here on).
            subprocess.run(["launchctl", "unload", LAUNCH_DAEMON], capture_output=True)
            r = subprocess.run(["launchctl", "load", "-w", LAUNCH_DAEMON],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return False, f"LaunchDaemon load failed: {r.stderr.strip()}"

            # Give wg-quick a moment to bring the interface up.
            for _ in range(10):
                if wg.tunnel_running():
                    return True, "installed"
                time.sleep(1)
            return True, "installed (interface not yet up — check logs)"
        except Exception as e:
            return False, f"Tunnel install failed: {e}"

    def uninstall_tunnel(self) -> bool:
        try:
            if os.path.exists(LAUNCH_DAEMON):
                subprocess.run(["launchctl", "unload", LAUNCH_DAEMON], capture_output=True)
                os.remove(LAUNCH_DAEMON)
            # Best-effort interface teardown.
            subprocess.run([WG_QUICK, "down", INSTALLED_CONF],
                           capture_output=True, env=_env())
            return True
        except Exception:
            return False

    def open_firewall(self, app_port: int) -> None:
        # macOS's application firewall is off by default and is app-based, not
        # port-based, so there's typically nothing to open. No-op.
        return

    # -- unprivileged ----------------------------------------------------------

    def elevate(self, argv: list[str], work_dir: str) -> tuple[bool, str]:
        # osascript raises the native admin auth dialog and runs the command as root.
        inner = " ".join(_shq(a) for a in argv)
        cmd = f"cd {_shq(work_dir)} && {inner}"
        script = f'do shell script {_osaq(cmd)} with administrator privileges'
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True)
            if r.returncode != 0:
                err = r.stderr.strip()
                if "-128" in err or "cancel" in err.lower():
                    return False, "Administrator permission was declined."
                return False, f"Couldn't start setup: {err}"
            return True, "elevated"
        except Exception as e:
            return False, f"Couldn't start setup: {e}"


def _plist() -> str:
    # WG_QUICK needs our bundle on PATH so it finds wg + wireguard-go.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tomebox.wireguard.{wg.TUNNEL_NAME}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{WG_QUICK}</string>
    <string>up</string>
    <string>{INSTALLED_CONF}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{BUNDLE_DIR}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
"""


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _osaq(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'