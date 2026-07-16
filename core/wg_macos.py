"""macOS WireGuard backend.

No kernel driver needed — macOS has utun built in, and WireGuard runs entirely in
userspace via wireguard-go. The tunnel runs as a root LaunchDaemon (auto-start at
boot). Elevation via osascript, which shows the standard macOS auth dialog.

BINARY RESOLUTION: bundle -> Homebrew -> PATH
---------------------------------------------
wg / wg-quick / wireguard-go are searched for in resources/macos/ first (the
eventual shipping story; needs signing+notarization), then Homebrew's bin dirs,
then PATH. A beta tester with `brew install wireguard-tools wireguard-go` works
today; a signed bundled build later is picked up automatically with no code change.

BASH NOTE
---------
wg-quick is a bash script requiring bash 4+. macOS ships bash 3.2 at /bin/bash, so
we front-load the Homebrew bin dirs (where `brew install bash` puts 5.x, and where
the wg tools live) ahead of the system dirs — in both the runtime env AND the
LaunchDaemon's PATH.

UNTESTED on real hardware beyond the current beta — written against documented
behaviour.
"""

import os
import shutil
import subprocess
import time
from typing import Optional

from core import wireguard as wg

BUNDLE_DIR = wg.resource_path("resources", "macos")

# Searched in order: bundled binaries win, then Homebrew, then PATH.
SEARCH_DIRS = [
    BUNDLE_DIR,
    "/opt/homebrew/bin",   # Homebrew, Apple Silicon
    "/usr/local/bin",      # Homebrew, Intel
]

LAUNCH_DAEMON = f"/Library/LaunchDaemons/com.tomebox.wireguard.{wg.TUNNEL_NAME}.plist"
INSTALLED_CONF = f"/usr/local/etc/wireguard/{wg.TUNNEL_NAME}.conf"


def _find(binary: str) -> Optional[str]:
    """Locate a WireGuard binary: bundle first, then Homebrew, then PATH."""
    for d in SEARCH_DIRS:
        p = os.path.join(d, binary)
        if os.path.exists(p):
            return p
    return shutil.which(binary)


def _tool_dir() -> str:
    """Directory holding the resolved tools — wg-quick needs its friends on PATH."""
    wgq = _find("wg-quick")
    return os.path.dirname(wgq) if wgq else "/usr/local/bin"


def _is_bundled() -> bool:
    wgq = _find("wg-quick")
    return bool(wgq) and wgq.startswith(BUNDLE_DIR)


def _path_dirs() -> list[str]:
    """PATH entries wg-quick needs: tool dir + Homebrew (for bash 4+) + system."""
    dirs = [BUNDLE_DIR, _tool_dir(), "/opt/homebrew/bin", "/usr/local/bin",
            "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return list(dict.fromkeys(d for d in dirs if d))   # dedupe, keep order


def _env() -> dict:
    """Runtime env for shelling out to wg-quick.

    wg-quick calls wg, wireguard-go, and bash by name — all must be on PATH. macOS
    /bin/bash is 3.2 and wg-quick needs 4+, so Homebrew's dirs go first.
    """
    e = os.environ.copy()
    e["PATH"] = os.pathsep.join(_path_dirs()) + os.pathsep + e.get("PATH", "")
    wgo = _find("wireguard-go")
    if wgo:
        e["WG_QUICK_USERSPACE_IMPLEMENTATION"] = wgo
    return e


class Backend:
    name = "macos"
    no_window_flag = 0   # macOS has no console-window concept

    def traceroute_cmd(self, host: str, max_hops: int) -> list:
        return ["traceroute", "-n", "-m", str(max_hops), "-w", "2", "-q", "1", host]

    def tools_available(self) -> bool:
        return all(_find(b) for b in ("wg", "wg-quick", "wireguard-go"))

    def can_auto_install(self) -> bool:
        # We never run brew on the user's behalf. Bundled binaries are the shipping
        # answer; until then the user installs the tools once.
        return _is_bundled()

    def install_hint(self) -> str:
        if self.tools_available():
            return "WireGuard is available; no separate install needed."
        return ("WireGuard tools aren't installed. Install them once with Homebrew, "
                "then try again:\n"
                "  brew install wireguard-tools wireguard-go bash")

    # -- elevated (running as root) --------------------------------------------

    def install_tools(self) -> tuple[bool, str]:
        if not self.tools_available():
            return False, self.install_hint()

        # Bundled binaries need to be made runnable and un-quarantined. Homebrew
        # ones are already fine — leave them alone.
        if _is_bundled():
            try:
                for b in ("wg", "wg-quick", "wireguard-go"):
                    p = os.path.join(BUNDLE_DIR, b)
                    if os.path.exists(p):
                        os.chmod(p, 0o755)
                        subprocess.run(["xattr", "-d", "com.apple.quarantine", p],
                                       capture_output=True)
            except Exception as e:
                return False, f"Couldn't prepare bundled binaries: {e}"

        return True, "ready"

    def install_tunnel(self, conf_path: str) -> tuple[bool, str]:
        wgq = _find("wg-quick")
        if not wgq:
            return False, self.install_hint()

        try:
            os.makedirs(os.path.dirname(INSTALLED_CONF), exist_ok=True)
            with open(conf_path) as src, open(INSTALLED_CONF, "w") as dst:
                dst.write(src.read())
            os.chmod(INSTALLED_CONF, 0o600)   # holds the server private key

            with open(LAUNCH_DAEMON, "w") as fh:
                fh.write(_plist(wgq))
            os.chmod(LAUNCH_DAEMON, 0o644)

            subprocess.run(["launchctl", "unload", LAUNCH_DAEMON], capture_output=True)
            r = subprocess.run(["launchctl", "load", "-w", LAUNCH_DAEMON],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return False, f"LaunchDaemon load failed: {r.stderr.strip()}"

            for _ in range(10):
                if wg.tunnel_running():
                    return True, "installed"
                time.sleep(1)

            return False, (
                "Tunnel installed but the interface didn't come up. Check "
                "/tmp/tomebox-wg.err for the wg-quick error."
            )
        except Exception as e:
            return False, f"Tunnel install failed: {e}"

    def uninstall_tunnel(self) -> bool:
        try:
            if os.path.exists(LAUNCH_DAEMON):
                subprocess.run(["launchctl", "unload", LAUNCH_DAEMON],
                               capture_output=True)
                os.remove(LAUNCH_DAEMON)
            wgq = _find("wg-quick")
            if wgq and os.path.exists(INSTALLED_CONF):
                subprocess.run([wgq, "down", INSTALLED_CONF],
                               capture_output=True, env=_env())
            return True
        except Exception:
            return False

    def open_firewall(self, app_port: int) -> None:
        # macOS's application firewall is off by default and filters by app, not
        # port — so there is nothing to open. (This avoids the Windows trap where a
        # working tunnel still can't reach the app port.)
        return

    # -- unprivileged ----------------------------------------------------------

    def elevate(self, argv: list[str], work_dir: str) -> tuple[bool, str]:
        """Raise the native macOS admin dialog and run setup as root."""
        inner = " ".join(_shq(a) for a in argv)
        cmd = f"cd {_shq(work_dir)} && {inner}"
        script = f"do shell script {_osaq(cmd)} with administrator privileges"
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True)
            if r.returncode != 0:
                err = (r.stderr or "").strip()
                if "-128" in err or "cancel" in err.lower():
                    return False, "Administrator permission was declined."
                return False, f"Couldn't start setup: {err or 'unknown error'}"
            return True, "elevated"
        except Exception as e:
            return False, f"Couldn't start setup: {e}"


def _plist(wg_quick_path: str) -> str:
    """LaunchDaemon — the macOS twin of a Windows auto-start service.

    PATH must front-load Homebrew (for bash 4+ and the tools), or the daemon fails
    at boot with the same bash-3 error even after setup 'succeeds'.
    """
    path_value = ":".join(_path_dirs())
    wgo = _find("wireguard-go") or ""
    wgo_entry = (
        f"    <key>WG_QUICK_USERSPACE_IMPLEMENTATION</key><string>{wgo}</string>\n"
        if wgo else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tomebox.wireguard.{wg.TUNNEL_NAME}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{wg_quick_path}</string>
    <string>up</string>
    <string>{INSTALLED_CONF}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{path_value}</string>
{wgo_entry}  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>/tmp/tomebox-wg.log</string>
  <key>StandardErrorPath</key><string>/tmp/tomebox-wg.err</string>
</dict>
</plist>
"""


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _osaq(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'