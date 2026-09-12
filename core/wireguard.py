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
import re
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from core.utils.net import DEFAULT_SERVER_PORT, resolve_server_port

TUNNEL_NAME = "tomebox"
SUBNET_PREFIX = "10.9.0"
SERVER_TUNNEL_IP = f"{SUBNET_PREFIX}.1"
LISTEN_PORT = 51820
DEFAULT_POOL_SIZE = 16
# _CGNAT_RE = re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")

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
    except Exception:
        return False
    finally:
        if s is not None:
            s.close()


def local_ip(override: str = "") -> str:
    """This machine's LAN address.

    NOT the route-to-8.8.8.8 trick — when TomeBox is inside a VPN's split-tunnel
    include list, that route leaves via the VPN adapter and returns the tunnel's
    internal address (10.2.0.2) rather than the LAN one.
    """
    if override:
        return override.strip()

    candidates: list[str] = []
    try:
        import psutil

        for _name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family != socket.AF_INET:
                    continue
                ip = a.address
                mask = a.netmask or ""
                if ip.startswith("127."):
                    continue
                # /32 = point-to-point, i.e. a VPN adapter, not a LAN interface.
                if mask == "255.255.255.255":
                    continue
                candidates.append(ip)
    except Exception:
        pass

    # Prefer a conventional private LAN range.
    for ip in candidates:
        if ip.startswith("192.168.") or ip.startswith("10."):
            return ip
        parts = ip.split(".")
        if len(parts) == 4 and parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            return ip
    if candidates:
        return candidates[0]

    # Last resort: the old route trick.
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
        "slots_free": sum(1 for s in pool if _slot_available(s, time.time())),
    }


# ------------------------------------------------------------- config building

def build_server_config(server_private, pool, listen_port=LISTEN_PORT):
    lines = [
        "[Interface]",
        f"PrivateKey = {server_private}",
        f"Address = {SERVER_TUNNEL_IP}/24",
        f"ListenPort = {listen_port}",
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


# def build_client_config(private_key: str, tunnel_ip: str,
#                         server_public: str, endpoint: str) -> str:
#     """The .conf a client imports.

#     AllowedIPs is scoped to the server alone — a split tunnel, so the client's other
#     traffic doesn't route through the user's house.
#     """
#     return "\n".join([
#         "[Interface]",
#         f"PrivateKey = {private_key}",
#         f"Address = {tunnel_ip}/32",
#         "",
#         "[Peer]",
#         f"PublicKey = {server_public}",
#         f"Endpoint = {endpoint}",
#         f"AllowedIPs = {SERVER_TUNNEL_IP}/32",
#         "PersistentKeepalive = 25",
#         "",
#     ])


def _setup_paths(data_dir: str) -> tuple[str, str]:
    wg_dir = os.path.join(data_dir, "wireguard")
    os.makedirs(wg_dir, exist_ok=True)
    return (
        os.path.join(wg_dir, f"{TUNNEL_NAME}.conf"),
        os.path.join(wg_dir, "setup_result.json"),
    )


# ------------------------------------------------------------------ the setup

def run_setup(data_dir: str, endpoint: str, app_port: int = DEFAULT_SERVER_PORT,
              pool_size: int = DEFAULT_POOL_SIZE, listen_port: int = LISTEN_PORT) -> dict:
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
            "confirmed_at": None,
            "reserved_otp": None,
            "reserved_until": None,
        })

    # 2. The tunnel config, with every pool peer baked in. THIS is what lets pairing
    #    run unprivileged later — no `wg set` is ever needed at runtime.
    with open(conf_path, "w", encoding="utf-8") as fh:
        fh.write(build_server_config(server_priv, pool, listen_port))
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
        be.open_firewall(app_port, listen_port)
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


def launch_setup(tomebox, app_port: Optional[int] = None,
                 pool_size: int = DEFAULT_POOL_SIZE) -> tuple[bool, str]:
    """THE UNPRIVILEGED HALF — raises the platform's privilege prompt exactly once.

    Blocks for up to ~5 minutes (installing WireGuard can be slow). Call from a
    worker thread, never from a UI callback.

    app_port defaults to whatever port the companion server is on (or will bind
    next start). Passing it explicitly is only for tests — the firewall rules and
    tunnel config written here are port-specific, so a wrong value fails silently
    at connect time, long after setup reports success.
    """
    if app_port is None:
        app_port = resolve_server_port(tomebox)

    listen_port = tomebox.settings.get("wg_listen_port", LISTEN_PORT)
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
        # The setup dialog normally writes wg_endpoint before we get here. This is a
        # fallback for a direct/scripted call — best-effort detection, and an honest
        # error if we can't work it out.
        host = detect_public_endpoint() or ""
        if not host:
            return False, (
                "No remote address is configured. Open “Set up remote access” and "
                "enter your public IP address or dynamic DNS hostname."
            )                  # status == "ok"

    endpoint = f"{host}:{listen_port}"
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

    argv += ["--wg-setup", data_dir, endpoint, str(app_port), str(pool_size), str(listen_port)]

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
        listen_port = int(argv[4]) if len(argv) > 4 else LISTEN_PORT
    except (IndexError, ValueError):
        return 1

    try:
        result = run_setup(data_dir, endpoint, app_port, pool_size, listen_port)
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

RESERVATION_TTL = 600   # matches the OTP lifetime minted in build_pairing_payload


def _reservation_active(slot: dict, now: float) -> bool:
    return bool(slot.get("reserved_otp")) and slot.get("reserved_until", 0) > now


def _slot_available(slot: dict, now: float) -> bool:
    """Free = not claimed by a paired device, and no live reservation on it."""
    return not slot.get("claimed") and not _reservation_active(slot, now)


def reserve_peer(tomebox, otp: str, device_name: str = "device",
                 ttl: int = RESERVATION_TTL) -> Optional[dict]:
    """Tentatively hold a pool slot against an OTP. None if the pool is exhausted.

    Reservations lapse with the OTP, so opening the pairing window and never
    scanning it costs nothing — the slot frees itself. Call confirm_peer() when
    the OTP is redeemed to make the hold permanent.
    """
    now = time.time()
    pool = tomebox.settings.get("wg_pool", [])

    # The same OTP asking twice (a redraw, a duplicate request) reuses its hold
    # rather than consuming a second slot.
    for slot in pool:
        if slot.get("reserved_otp") == otp and not slot.get("claimed"):
            slot["reserved_until"] = now + ttl
            tomebox.settings["wg_pool"] = pool
            tomebox.db.save_settings(tomebox.settings)
            return slot

    for slot in pool:
        if _slot_available(slot, now):
            slot["reserved_otp"] = otp
            slot["reserved_until"] = now + ttl
            slot["device_name"] = device_name
            tomebox.settings["wg_pool"] = pool
            tomebox.db.save_settings(tomebox.settings)
            return slot

    return None


def rekey_reservation(tomebox, old_otp: str, new_otp: str,
                      ttl: int = RESERVATION_TTL) -> bool:
    """Move a live reservation onto a re-minted OTP.

    The pairing dialog's Refresh button mints a new OTP without rebuilding the
    payload, so the slot's hold has to follow it or confirm_peer finds nothing.
    """
    now = time.time()
    pool = tomebox.settings.get("wg_pool", [])
    for slot in pool:
        if slot.get("reserved_otp") == old_otp and not slot.get("claimed"):
            slot["reserved_otp"] = new_otp
            slot["reserved_until"] = now + ttl
            tomebox.settings["wg_pool"] = pool
            tomebox.db.save_settings(tomebox.settings)
            return True
    return False


def confirm_peer(tomebox, otp: str,
                 device_name: Optional[str] = None) -> Optional[dict]:
    """Promote an OTP's reservation to a permanent claim. Call on redemption.

    Returns None when the OTP had no reservation — the normal LAN-only case, not
    an error. Expiry is deliberately not rechecked here: the caller has already
    validated the OTP against _active_otps, and the two share a lifetime.
    """
    now = time.time()
    pool = tomebox.settings.get("wg_pool", [])
    for slot in pool:
        if slot.get("reserved_otp") == otp:
            slot["claimed"] = True
            slot["claimed_at"] = now
            slot["confirmed_at"] = now
            if device_name:
                slot["device_name"] = device_name
            slot["reserved_otp"] = None
            slot["reserved_until"] = None
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
            slot.update({
                "claimed": False,
                "device_name": None,
                "claimed_at": None,
                "confirmed_at": None,
                "reserved_otp": None,
                "reserved_until": None,
            })
            tomebox.settings["wg_pool"] = pool
            tomebox.db.save_settings(tomebox.settings)
            return True
    return False


def build_pairing_payload(tomebox, port: Optional[int] = None) -> tuple[str, str]:
    """Single source of truth for pairing — used by BOTH the desktop dialog and the
    web endpoint, so the two can never drift apart.

    Returns (app_payload_json, otp).

    The tunnel is delivered inside the payload's "wg" block, which the mobile app
    builds its own interface from. The old standalone WireGuard .conf QR is gone.

    Degrades gracefully: with no remote access configured, the payload is LAN-only
    and everything works exactly as it did before WireGuard existed.

    port defaults to the live/persisted server port. The web endpoint passes the
    request's own port explicitly, which is more accurate still — it reflects the
    port the client actually reached us on.
    """
    if port is None:
        port = resolve_server_port(tomebox)

    now = time.time()
    if not hasattr(tomebox, "_active_otps"):
        tomebox._active_otps = {}
    tomebox._active_otps = {k: v for k, v in tomebox._active_otps.items() if v > now}

    otp = secrets.token_hex(4)
    tomebox._active_otps[otp] = now + 600

    lan = local_ip(tomebox.settings.get("lan_address", ""))
    payload = {"v": 1, "otp": otp, "lan": f"http://{lan}:{port}"}
    server_pub = tomebox.settings.get("wg_server_public")
    endpoint = tomebox.settings.get("wg_endpoint")

    if server_pub and endpoint and tunnel_running():
        slot = reserve_peer(tomebox, otp, device_name=f"device-{int(now)}")
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

    return json.dumps(payload), otp
