"""
WireGuard automation for TomeBox remote access.

WHY IT IS SPLIT IN TWO
----------------------
Every wg.exe operation on Windows requires Administrator, and installing a tunnel
service requires more than that. But `wireguard.exe /installtunnelservice` registers
the tunnel as an AUTO-START Windows service — so once it is installed, Windows
brings the tunnel up at boot and TomeBox never has to touch it again.

    SETUP  (elevated, ONCE, behind a single UAC prompt)
        - generate the server keypair
        - generate a POOL of device slots (keypair + tunnel IP each)
        - write the tunnel .conf containing every pool peer
        - install the tunnel as an auto-start Windows service
        - open the firewall for the TomeBox port and the WireGuard port
        - hand the results back to the unprivileged parent via a JSON file

    RUNTIME  (unprivileged, EVERY launch, forever)
        - detect the tunnel service      -> `sc query`, needs no admin
        - read server public key, endpoint, pool -> from TomeBox settings
        - pair a device -> claim the next unclaimed pool slot
        - NO wg.exe calls at all

The peer pool is what keeps PAIRING unprivileged. Adding a peer at runtime would
mean `wg set`, which needs admin — so instead every device slot is generated during
the one-time setup and baked into the tunnel config up front. Pairing just marks a
slot as claimed in the DB.

NOTHING about the WireGuard configuration is hardcoded. The server key, the peers,
and the endpoint are all generated or detected. The only constant is where Windows
puts the WireGuard binaries.

SECURITY NOTE
-------------
Unclaimed pool slots hold their private keys in the TomeBox DB until handed out via
QR. Anyone with the DB could impersonate an unclaimed device. Acceptable for a
self-hosted app (DB access is already game over), but worth encrypting with DPAPI
before this ships widely.
"""

import base64
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

# The only hardcoded values: where Windows installs WireGuard.
WIREGUARD_DIR = r"C:\Program Files\WireGuard"
WG_EXE = os.path.join(WIREGUARD_DIR, "wg.exe")
WIREGUARD_EXE = os.path.join(WIREGUARD_DIR, "wireguard.exe")

TUNNEL_NAME = "TomeBox"
SERVICE_NAME = f"WireGuardTunnel${TUNNEL_NAME}"

SUBNET_PREFIX = "10.9.0"
SERVER_TUNNEL_IP = f"{SUBNET_PREFIX}.1"
LISTEN_PORT = 51820
DEFAULT_POOL_SIZE = 16

CREATE_NO_WINDOW = 0x08000000

# ---------------------------------------------------------------- key generation

def generate_keypair() -> tuple[str, str]:
    """(private_b64, public_b64) — raw 32-byte X25519, WireGuard's key format.

    Pure Python: no wg.exe, and therefore no elevation.
    """
    private = X25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode(),
        base64.b64encode(public_raw).decode(),
    )


# -------------------------------------------------------------------- detection
# Everything here works UNPRIVILEGED — TomeBox must be able to report remote-access
# status without asking for admin.

def is_wireguard_installed() -> bool:
    return os.path.exists(WG_EXE) and os.path.exists(WIREGUARD_EXE)


def is_elevated() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _sc_query() -> tuple[bool, str]:
    """`sc query` on our tunnel service. Works without elevation, unlike `wg show`."""
    try:
        result = subprocess.run(
            ["sc", "query", SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0, result.stdout
    except Exception:
        return False, ""


def tunnel_service_exists() -> bool:
    exists, _ = _sc_query()
    return exists


def tunnel_service_running() -> bool:
    exists, out = _sc_query()
    return exists and "RUNNING" in out


def detect_public_endpoint() -> Optional[str]:
    """Best-effort public IP. Returns None on CGNAT/private ranges, where inbound
    connections are impossible and remote access cannot work at all."""
    try:
        import httpx
        ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
        parts = ip.split(".")
        if len(parts) != 4:
            return None
        first, second = int(parts[0]), int(parts[1])
        if first in (10, 127):
            return None
        if first == 192 and second == 168:
            return None
        if first == 172 and 16 <= second <= 31:
            return None
        if first == 100 and 64 <= second <= 127:
            return None  # CGNAT — no inbound possible
        return ip
    except Exception:
        return None


def get_status(tomebox) -> dict:
    """Everything the UI needs in order to decide what to show. Unprivileged."""
    pool = tomebox.settings.get("wg_pool", [])
    return {
        "wireguard_installed": is_wireguard_installed(),
        "tunnel_installed": tunnel_service_exists(),
        "tunnel_running": tunnel_service_running(),
        "configured": bool(tomebox.settings.get("wg_server_public")),
        "endpoint": tomebox.settings.get("wg_endpoint", ""),
        "slots_total": len(pool),
        "slots_free": sum(1 for s in pool if not s.get("claimed")),
    }


# --------------------------------------------------------------- config building

def build_server_config(server_private: str, pool: list[dict]) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {server_private}",
        f"Address = {SERVER_TUNNEL_IP}/24",
        f"ListenPort = {LISTEN_PORT}",
        "",
    ]
    for slot in pool:
        lines += [
            "[Peer]",
            f"# slot {slot['tunnel_ip']}",
            f"PublicKey = {slot['public_key']}",
            f"AllowedIPs = {slot['tunnel_ip']}/32",
            "",
        ]
    return "\n".join(lines)


def build_client_config(private_key: str, tunnel_ip: str,
                        server_public: str, endpoint: str) -> str:
    """The plaintext .conf the WireGuard app imports by QR scan.

    AllowedIPs is scoped to the server alone — a split tunnel, so the phone's other
    traffic does not route through the user's house.
    """
    return "\n".join([
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {tunnel_ip}/32",
        "",
        "[Peer]",
        f"PublicKey = {server_public}",
        f"Endpoint = {endpoint}",
        f"AllowedIPs = {SERVER_TUNNEL_IP}/32",
        "PersistentKeepalive = 25",
        "",
    ])


# --------------------------------------------------------------- elevated setup

def _setup_paths(data_dir: str) -> tuple[str, str]:
    wg_dir = os.path.join(data_dir, "wireguard")
    os.makedirs(wg_dir, exist_ok=True)
    return (
        os.path.join(wg_dir, f"{TUNNEL_NAME}.conf"),
        os.path.join(wg_dir, "setup_result.json"),
    )

def _resource_path(*parts) -> str:
    """Path to a bundled resource. PyInstaller unpacks to sys._MEIPASS at runtime."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def install_wireguard() -> tuple[bool, str]:
    """Silently install the bundled WireGuard MSI. MUST already be elevated.

    /qn = fully silent, no UI. The user sees nothing — this rides on the single UAC
    prompt they already approved for the tunnel setup.
    """
    if is_wireguard_installed():
        return True, "already installed"

    msi = _find_wireguard_msi()
    if not msi:
        return False, (
            f"No bundled WireGuard installer for this system ({_os_arch()}). "
            "Install WireGuard manually from wireguard.com/install and try again."
        )
    if not os.path.exists(msi):
        return False, f"Bundled WireGuard installer not found at {msi}"

    try:
        result = subprocess.run(
            ["msiexec", "/i", msi, "/qn", "/norestart", "DO_NOT_LAUNCH=1"],
            capture_output=True, text=True, timeout=300,
            creationflags=CREATE_NO_WINDOW,
        )
        # msiexec: 0 = ok, 3010 = ok but wants a reboot (fine, the driver loads anyway)
        if result.returncode not in (0, 3010):
            return False, f"WireGuard install failed (msiexec code {result.returncode})"
    except Exception as e:
        return False, f"WireGuard install failed: {e}"

    # msiexec returns before the files are necessarily on disk. Wait for wg.exe.
    for _ in range(30):
        if is_wireguard_installed():
            return True, "installed"
        time.sleep(1)

    return False, "WireGuard installed but its binaries weren't found — a reboot may be needed."


def _os_arch() -> str:
    """OS architecture, not process architecture.

    PROCESSOR_ARCHITEW6432 is set when a 32-bit process runs on 64-bit Windows —
    without checking it, a 32-bit build would think the machine is x86.
    """
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE", "")).upper()
    return {"AMD64": "amd64", "ARM64": "arm64", "X86": "x86"}.get(arch, "amd64")


def _find_wireguard_msi() -> Optional[str]:
    """Locate the bundled MSI for this machine's architecture.

    Globbed rather than hardcoded, so bumping the WireGuard version doesn't
    require a code change.
    """
    import glob

    arch = _os_arch()
    pattern = _resource_path("resources", f"wireguard-{arch}-*.msi")
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None   # highest version if several

def run_setup(data_dir: str, endpoint: str, app_port: int = 8000,
              pool_size: int = DEFAULT_POOL_SIZE) -> dict:
    """THE ELEVATED HALF — runs inside the admin child process.

    Installs WireGuard if absent, generates everything, installs the tunnel as an
    auto-start service, and opens the firewall. Writes a JSON handoff for the
    unprivileged parent.
    """
    conf_path, result_path = _setup_paths(data_dir)

    def fail(msg: str) -> dict:
        result = {"ok": False, "error": msg}
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        return result

    # 0. WireGuard itself. We're already elevated, so this is free to the user —
    #    no second UAC prompt, no separate install step, nothing to click.
    ok, msg = install_wireguard()
    if not ok:
        return fail(msg)

    # 1. Keys: the server, plus a pool of device slots.
    server_priv, server_pub = generate_keypair()

    pool = []
    for i in range(pool_size):
        priv, pub = generate_keypair()
        pool.append({
            "tunnel_ip": f"{SUBNET_PREFIX}.{i + 2}",   # .1 is the server
            "private_key": priv,
            "public_key": pub,
            "claimed": False,
            "device_name": None,
            "claimed_at": None,
        })

    # 2. The tunnel config, with every pool peer baked in. This is what lets pairing
    #    run unprivileged later — no `wg set` is ever needed at runtime.
    with open(conf_path, "w", encoding="utf-8") as fh:
        fh.write(build_server_config(server_priv, pool))

    # 3. Idempotent: tear down any previous install first.
    if tunnel_service_exists():
        subprocess.run(
            [WIREGUARD_EXE, "/uninstalltunnelservice", TUNNEL_NAME],
            capture_output=True, timeout=20,
        )
        time.sleep(2)

    # 4. Register the tunnel as an AUTO-START Windows service. This is the whole
    #    reason elevation is only needed once: Windows brings the tunnel up on every
    #    boot from here on, with no involvement from TomeBox.
    install = subprocess.run(
        [WIREGUARD_EXE, "/installtunnelservice", conf_path],
        capture_output=True, text=True, timeout=30,
    )
    if install.returncode != 0:
        return fail(f"Tunnel install failed: {install.stderr.strip() or 'unknown error'}")

    # 5. Firewall. Windows classifies the new WireGuard adapter as a Public network
    #    and blocks inbound by default — the single most common reason a working
    #    tunnel still can't reach TomeBox. Open it while we still have admin.
    for proto, port, label in (
        ("TCP", app_port, "TomeBox (WireGuard)"),
        ("UDP", LISTEN_PORT, "WireGuard (TomeBox)"),
    ):
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={label}", "dir=in", "action=allow",
             f"protocol={proto}", f"localport={port}"],
            capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )

    result = {
        "ok": True,
        "error": None,
        "wg_server_public": server_pub,
        "wg_endpoint": endpoint,
        "wg_pool": pool,
        "wg_conf_path": conf_path,
        "created": time.time(),
    }

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)

    return result


def launch_setup(tomebox, app_port: int = 8000,
                 pool_size: int = DEFAULT_POOL_SIZE) -> tuple[bool, str]:
    """THE UNPRIVILEGED HALF — triggers the single UAC prompt.

    Re-invokes TomeBox with --wg-setup as an elevated child, waits for the handoff,
    and merges the results into settings. After this succeeds, remote access is live
    permanently and no elevation is ever needed again.
    """
    # if not is_wireguard_installed():
    #     return False, (
    #         "WireGuard for Windows isn't installed. Install it from "
    #         "wireguard.com/install, then try again."
    #     )

    # Endpoint: use whatever the user configured, else auto-detect.
    configured = tomebox.settings.get("wg_endpoint", "")
    endpoint_host = configured.split(":")[0] if configured else ""
    if not endpoint_host:
        detected = detect_public_endpoint()
        if not detected:
            return False, (
                "Couldn't find a usable public IP address. Your ISP may be using "
                "CGNAT, which makes inbound connections impossible. If you have a "
                "dynamic DNS hostname, set it in settings and try again."
            )
        endpoint_host = detected

    endpoint = f"{endpoint_host}:{LISTEN_PORT}"
    data_dir = tomebox.base_dir
    _, result_path = _setup_paths(data_dir)

    if os.path.exists(result_path):
        os.remove(result_path)

    # Re-invoke ourselves elevated. Handles both `python main.py` and a frozen exe.
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = f'--wg-setup "{data_dir}" "{endpoint}" {app_port} {pool_size}'
    else:
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        script = os.path.abspath(sys.argv[0])
        params = f'"{script}" --wg-setup "{data_dir}" "{endpoint}" {app_port} {pool_size}'
        work_dir = os.path.dirname(script)

    try:
        # The "runas" verb is what raises the UAC prompt.
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, work_dir,
            0,  
        )
        if rc <= 32:
            return False, "Administrator permission was declined. Remote access not set up."
    except Exception as e:
        return False, f"Couldn't start the setup process: {e}"

    # Wait for the elevated child to drop its handoff file.
    for _ in range(90):
        if os.path.exists(result_path):
            break
        time.sleep(1)
    else:
        return False, "Setup timed out. Check that WireGuard installed correctly."

    with open(result_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)

    if not result.get("ok"):
        return False, f"Tunnel install failed: {result.get('error') or 'unknown error'}"

    tomebox.settings["wg_server_public"] = result["wg_server_public"]
    tomebox.settings["wg_endpoint"] = result["wg_endpoint"]
    tomebox.settings["wg_pool"] = result["wg_pool"]
    tomebox.settings["wg_conf_path"] = result["wg_conf_path"]
    tomebox.db.save_settings(tomebox.settings)

    os.remove(result_path)
    return True, "Remote access is ready. You can now pair devices from anywhere."


def setup_entrypoint(argv: list[str]) -> int:
    try:
        data_dir, endpoint = argv[0], argv[1]
        app_port, pool_size = int(argv[2]), int(argv[3])
    except (IndexError, ValueError):
        return 1

    try:
        result = run_setup(data_dir, endpoint, app_port, pool_size)
        return 0 if result.get("ok") else 1
    except Exception as e:
        # Always hand SOMETHING back, or the parent just times out blind.
        import traceback
        _, result_path = _setup_paths(data_dir)
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump({
                "ok": False,
                "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            }, fh)
        return 1


# ------------------------------------------------------------------------ pairing
# Unprivileged. No wg.exe calls — every peer already exists in the tunnel config.

def claim_peer(tomebox, device_name: str = "device") -> Optional[dict]:
    """Hand out the next unclaimed pool slot. None if the pool is exhausted."""
    pool = tomebox.settings.get("wg_pool", [])
    for slot in pool:
        if not slot.get("claimed"):
            slot["claimed"] = True
            slot["device_name"] = device_name
            slot["claimed_at"] = time.time()
            tomebox.settings["wg_pool"] = pool
            tomebox.db.save_settings(tomebox.settings)
            return slot
    return None


def release_peer(tomebox, tunnel_ip: str) -> bool:
    """Un-claim a slot so its IP can be reissued.

    NOTE: this does NOT revoke the old device — its key is still in the tunnel
    config. True revocation means rewriting the .conf and restarting the service,
    which needs elevation. Treat this as 'reuse the slot', not 'revoke access'.
    """
    pool = tomebox.settings.get("wg_pool", [])
    for slot in pool:
        if slot.get("tunnel_ip") == tunnel_ip:
            slot.update({"claimed": False, "device_name": None, "claimed_at": None})
            tomebox.settings["wg_pool"] = pool
            tomebox.db.save_settings(tomebox.settings)
            return True
    return False


def build_pairing_payload(tomebox, port: int = 8000) -> tuple[str, Optional[str], str]:
    """Single source of truth for pairing — used by BOTH the desktop dialog and the
    web endpoint, so the two can never drift apart again.

    Returns (app_payload_json, wg_client_config_or_None, otp).

    Degrades gracefully: if remote access isn't set up, the payload is LAN-only and
    everything works exactly as it did before WireGuard existed.
    """
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    now = time.time()
    if not hasattr(tomebox, "_active_otps"):
        tomebox._active_otps = {}
    tomebox._active_otps = {k: v for k, v in tomebox._active_otps.items() if v > now}

    otp = secrets.token_hex(4)
    tomebox._active_otps[otp] = now + 600

    payload = {"v": 1, "otp": otp, "lan": f"http://{local_ip}:{port}"}
    wg_conf = None

    server_pub = tomebox.settings.get("wg_server_public")
    endpoint = tomebox.settings.get("wg_endpoint")

    if server_pub and endpoint and tunnel_service_running():
        slot = claim_peer(tomebox, device_name=f"device-{int(now)}")
        if slot:
            payload["vpn"] = f"http://{SERVER_TUNNEL_IP}:{port}"
            payload["wg"] = {
                "private_key": slot["private_key"],
                "address": f"{slot['tunnel_ip']}/32",
                "peer_public_key": server_pub,
                "endpoint": endpoint,
                "allowed_ips": f"{SERVER_TUNNEL_IP}/32",
                "keepalive": 25,
            }
            wg_conf = build_client_config(
                private_key=slot["private_key"],
                tunnel_ip=slot["tunnel_ip"],
                server_public=server_pub,
                endpoint=endpoint,
            )

    return json.dumps(payload), wg_conf, otp