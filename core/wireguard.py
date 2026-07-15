"""
WireGuard automation for TomeBox remote access — portable core.

EVERYTHING PLATFORM-SPECIFIC LIVES IN A BACKEND
-----------------------------------------------
This module holds the parts that are identical everywhere: key generation, the peer
pool, config text, the pairing payload. Each OS supplies a backend (wg_windows.py,
wg_macos.py, wg_linux.py) implementing six operations:

    tools_available()   is WireGuard present?              unprivileged
    install_tools()     put it there                       ELEVATED
    install_tunnel()    register an auto-start service     ELEVATED
    uninstall_tunnel()  remove it                          ELEVATED
    open_firewall()     allow inbound on the tunnel        ELEVATED, best-effort
    elevate()           raise a privilege prompt           unprivileged

THE ONE-TIME-ELEVATION MODEL
----------------------------
Configuring a tunnel needs root/admin on every OS. Rather than run TomeBox elevated
forever, we elevate ONCE and register the tunnel as an OS-managed auto-start service
(Windows service / macOS LaunchDaemon / systemd unit). The OS then brings the tunnel
up at every boot, and TomeBox never needs privilege again.

Pairing must also be unprivileged, so we pre-generate a POOL of device slots during
that same elevated setup and bake them all into the tunnel config. Pairing then just
hands out a slot — no `wg set`, no prompt.

SECURITY NOTE
-------------
Unclaimed pool slots hold their private keys in the TomeBox DB until handed out via
QR. Anyone with the DB could impersonate an unclaimed device. Acceptable for a
self-hosted app (DB access is already game over), but worth encrypting at rest.
"""

import json
import os
import platform
import secrets
import socket
import sys
import time
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

TUNNEL_NAME = "tomebox"
SUBNET_PREFIX = "10.9.0"
SERVER_TUNNEL_IP = f"{SUBNET_PREFIX}.1"
LISTEN_PORT = 51820
DEFAULT_POOL_SIZE = 16


# -------------------------------------------------------------------- backend

def _backend():
    """The platform backend. Imported lazily so a missing OS module can't break
    startup on the platforms that do work."""
    system = platform.system()
    if system == "Windows":
        from core import wg_windows
        return wg_windows.Backend()
    if system == "Darwin":
        from core import wg_macos
        return wg_macos.Backend()
    if system == "Linux":
        from core import wg_linux
        return wg_linux.Backend()
    raise RuntimeError(f"Remote access isn't supported on {system}.")


def backend_name() -> str:
    try:
        return _backend().name
    except Exception:
        return "unsupported"


def resource_path(*parts) -> str:
    """Path to a bundled resource, for both source and PyInstaller-frozen runs."""
    base = getattr(
        sys, "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    return os.path.join(base, *parts)


# -------------------------------------------------------------- key generation

def generate_keypair() -> tuple[str, str]:
    """(private_b64, public_b64) — raw 32-byte X25519, WireGuard's key format.

    Pure Python. No wg binary, no elevation, identical on every platform.
    """
    import base64

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


# ------------------------------------------------------------------ detection

def tunnel_running() -> bool:
    """Is our tunnel actually up?

    Portable and unprivileged: try to bind a socket to the tunnel's IP. That only
    succeeds if the address is assigned to a local interface — which is exactly what
    "the tunnel is up" means. Beats `sc query` / `launchctl` / `systemctl` because
    it's ONE implementation for all three OSes, and it tests the real thing rather
    than whether a service is merely registered.
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((SERVER_TUNNEL_IP, 0))   # port 0 = ephemeral, so never conflicts
        return True
    except OSError:
        return False
    finally:
        if s is not None:
            s.close()


def local_ip() -> str:
    """This machine's LAN address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def detect_public_endpoint() -> Optional[str]:
    """Best-effort public IP. None on CGNAT/private ranges, where inbound
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
    try:
        be = _backend()
        supported = True
        tools = be.tools_available()
        can_auto = be.can_auto_install()
        hint = be.install_hint()
    except Exception:
        supported, tools, can_auto, hint = False, False, False, ""

    return {
        "platform": platform.system(),
        "supported": supported,
        "tools_available": tools,
        "can_auto_install": can_auto,
        "install_hint": hint,
        "tunnel_running": tunnel_running(),
        "configured": bool(tomebox.settings.get("wg_server_public")),
        "endpoint": tomebox.settings.get("wg_endpoint", ""),
        "slots_total": len(pool),
        "slots_free": sum(1 for s in pool if not s.get("claimed")),
    }


# ------------------------------------------------------------- config building

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
    """The .conf a client imports.

    AllowedIPs is scoped to the server alone — a split tunnel, so the client's other
    traffic doesn't route through the user's house.
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


def _setup_paths(data_dir: str) -> tuple[str, str]:
    wg_dir = os.path.join(data_dir, "wireguard")
    os.makedirs(wg_dir, exist_ok=True)
    return (
        os.path.join(wg_dir, f"{TUNNEL_NAME}.conf"),
        os.path.join(wg_dir, "setup_result.json"),
    )


# ------------------------------------------------------------------ the setup

def run_setup(data_dir: str, endpoint: str, app_port: int = 8000,
              pool_size: int = DEFAULT_POOL_SIZE) -> dict:
    """THE ELEVATED HALF — runs in the privileged child process.

    Platform-agnostic orchestration; the backend does the OS-specific parts.
    """
    conf_path, result_path = _setup_paths(data_dir)

    def fail(msg: str) -> dict:
        result = {"ok": False, "error": msg}
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        return result

    try:
        be = _backend()
    except Exception as e:
        return fail(str(e))

    # 0. WireGuard itself. We're already elevated, so this is free to the user —
    #    no second prompt, no separate install step.
    if not be.tools_available():
        ok, msg = be.install_tools()
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

    # 2. The tunnel config, with every pool peer baked in. THIS is what lets pairing
    #    run unprivileged later — no `wg set` is ever needed at runtime.
    with open(conf_path, "w", encoding="utf-8") as fh:
        fh.write(build_server_config(server_priv, pool))
    try:
        os.chmod(conf_path, 0o600)   # contains the server private key
    except Exception:
        pass

    # 3. Idempotent: tear down any previous install.
    be.uninstall_tunnel()

    # 4. Register the tunnel as an OS-managed auto-start service. This is the whole
    #    reason elevation is needed only once — the OS brings it up on every boot.
    ok, msg = be.install_tunnel(conf_path)
    if not ok:
        return fail(msg)

    # 5. Firewall. Best-effort: a failure is user-recoverable, and on some platforms
    #    there is nothing to do.
    try:
        be.open_firewall(app_port)
    except Exception:
        pass

    result = {
        "ok": True,
        "error": None,
        "wg_server_public": server_pub,
        "wg_endpoint": endpoint,
        "wg_pool": pool,
        "wg_conf_path": conf_path,
        "backend": be.name,
        "created": time.time(),
    }

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)

    return result


def launch_setup(tomebox, app_port: int = 8000,
                 pool_size: int = DEFAULT_POOL_SIZE) -> tuple[bool, str]:
    """THE UNPRIVILEGED HALF — raises the platform's privilege prompt exactly once.

    Blocks for up to ~5 minutes (installing WireGuard can be slow). Call from a
    worker thread, never from a UI callback.
    """
    try:
        be = _backend()
    except Exception as e:
        return False, str(e)

    if not be.tools_available() and not be.can_auto_install():
        return False, be.install_hint()

    # Endpoint: whatever the user configured, else auto-detect.
    configured = tomebox.settings.get("wg_endpoint", "")
    host = configured.split(":")[0] if configured else ""
    if not host:
        detected = detect_public_endpoint()
        if not detected:
            return False, (
                "Couldn't find a usable public IP address. Your ISP may be using "
                "CGNAT, which makes inbound connections impossible. If you have a "
                "dynamic DNS hostname, set it in settings and try again."
            )
        host = detected

    endpoint = f"{host}:{LISTEN_PORT}"
    data_dir = tomebox.base_dir

    # Create the directory UNPRIVILEGED, before elevating. On POSIX the elevated
    # child runs as root; if root created this directory, the unprivileged parent
    # could not later delete the handoff file inside it.
    _, result_path = _setup_paths(data_dir)
    if os.path.exists(result_path):
        os.remove(result_path)

    # Re-invoke ourselves with the setup flag, elevated.
    if getattr(sys, "frozen", False):
        argv = [sys.executable]
        work_dir = os.path.dirname(sys.executable)
    else:
        script = os.path.abspath(sys.argv[0])
        argv = [sys.executable, script]
        work_dir = os.path.dirname(script)

    argv += ["--wg-setup", data_dir, endpoint, str(app_port), str(pool_size)]

    ok, msg = be.elevate(argv, work_dir)
    if not ok:
        return False, msg

    # Wait for the elevated child to drop its handoff file.
    for _ in range(300):
        if os.path.exists(result_path):
            break
        time.sleep(1)
    else:
        return False, (
            "Setup timed out — the elevated process never reported back.\n\n"
            "To see the real error, run this yourself with admin/root:\n  "
            + " ".join(argv)
        )

    with open(result_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)

    if not result.get("ok"):
        return False, f"Setup failed:\n{result.get('error') or 'unknown error'}"

    tomebox.settings["wg_server_public"] = result["wg_server_public"]
    tomebox.settings["wg_endpoint"] = result["wg_endpoint"]
    tomebox.settings["wg_pool"] = result["wg_pool"]
    tomebox.settings["wg_conf_path"] = result["wg_conf_path"]
    tomebox.db.save_settings(tomebox.settings)

    try:
        os.remove(result_path)
    except OSError:
        pass   # root-owned on POSIX; harmless if it lingers

    return True, "Remote access is ready. You can now pair devices from anywhere."


def setup_entrypoint(argv: list[str]) -> int:
    """Entry point for the elevated child. main.py routes here when TomeBox is
    launched with:  --wg-setup <data_dir> <endpoint> <app_port> <pool_size>
    """
    try:
        data_dir, endpoint = argv[0], argv[1]
        app_port, pool_size = int(argv[2]), int(argv[3])
    except (IndexError, ValueError):
        return 1

    try:
        result = run_setup(data_dir, endpoint, app_port, pool_size)
        return 0 if result.get("ok") else 1
    except Exception as e:
        # Always report SOMETHING, or the parent just times out blind.
        import traceback
        try:
            _, result_path = _setup_paths(argv[0])
            with open(result_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                }, fh)
        except Exception:
            pass
        return 1


# --------------------------------------------------------------------- pairing
# Unprivileged everywhere. No wg calls — every peer already exists in the config.

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

    NOTE: does NOT revoke the old device — its key is still in the tunnel config.
    True revocation means rewriting the .conf and restarting the service, which
    needs elevation. This is 'reuse the slot', not 'revoke access'.
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
    web endpoint, so the two can never drift apart.

    Returns (app_payload_json, wg_client_config_or_None, otp).

    Degrades gracefully: with no remote access configured, the payload is LAN-only
    and everything works exactly as it did before WireGuard existed.
    """
    now = time.time()
    if not hasattr(tomebox, "_active_otps"):
        tomebox._active_otps = {}
    tomebox._active_otps = {k: v for k, v in tomebox._active_otps.items() if v > now}

    otp = secrets.token_hex(4)
    tomebox._active_otps[otp] = now + 600

    payload = {"v": 1, "otp": otp, "lan": f"http://{local_ip()}:{port}"}
    wg_conf = None

    server_pub = tomebox.settings.get("wg_server_public")
    endpoint = tomebox.settings.get("wg_endpoint")

    if server_pub and endpoint and tunnel_running():
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