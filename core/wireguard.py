"""
WireGuard peer management for TomeBox remote access.

Owns:
  - X25519 keypair generation (no dependency on wg.exe)
  - Tunnel IP allocation
  - Hot-adding peers to the running tunnel
  - Building the client config text (for the WireGuard-app QR)

The DB/settings store is the source of truth for the peer list. The .conf file is
derived from it, so a corrupt/edited .conf can always be regenerated.

Peer records only ever store the device's PUBLIC key. The private key is generated,
handed to the QR, and discarded.
"""

import base64
import subprocess
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

WG_EXE = r"C:\Program Files\WireGuard\wg.exe"
TUNNEL_NAME = "TomeBox_WAN_TEST"
SUBNET_PREFIX = "10.9.0"
SERVER_TUNNEL_IP = f"{SUBNET_PREFIX}.1"
LISTEN_PORT = 51820


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) in WireGuard's format.

    WireGuard keys are raw 32-byte X25519, base64-encoded. Generating them here
    avoids shelling out to wg.exe and works identically on any platform.
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


def get_server_public_key() -> Optional[str]:
    """Read the running tunnel's public key. None if the tunnel isn't up."""
    try:
        result = subprocess.run(
            [WG_EXE, "show", TUNNEL_NAME, "public-key"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        print(f"[WG] rc={result.returncode} out={result.stdout!r} err={result.stderr!r}")
        if result.returncode != 0:
            return None
        key = result.stdout.strip()
        return key or None
    except Exception as e:
        print(f"[WG] exception: {type(e).__name__}: {e}")
        return None


def is_tunnel_up() -> bool:
    """True if the WireGuard tunnel service is running."""
    return get_server_public_key() is not None


def allocate_tunnel_ip(existing_peers: list[dict]) -> str:
    """Next free address in the tunnel subnet. .1 is the server, so start at .2."""
    used = set()
    for peer in existing_peers:
        addr = peer.get("tunnel_ip", "")
        if addr.startswith(f"{SUBNET_PREFIX}."):
            try:
                used.add(int(addr.rsplit(".", 1)[1]))
            except ValueError:
                pass

    for octet in range(2, 255):
        if octet not in used:
            return f"{SUBNET_PREFIX}.{octet}"

    raise RuntimeError("Tunnel subnet exhausted (254 devices).")


def add_peer(public_key: str, tunnel_ip: str) -> bool:
    """Hot-add a peer to the running tunnel. No restart, no dropped connections.

    Returns False if the tunnel isn't running or wg.exe rejects it. The caller
    should still persist the peer so it can be written into the .conf on restart.
    """
    try:
        result = subprocess.run(
            [
                WG_EXE, "set", TUNNEL_NAME,
                "peer", public_key,
                "allowed-ips", f"{tunnel_ip}/32",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def remove_peer(public_key: str) -> bool:
    """Revoke a device by removing its peer from the running tunnel."""
    try:
        result = subprocess.run(
            [WG_EXE, "set", TUNNEL_NAME, "peer", public_key, "remove"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def build_client_config(
    private_key: str,
    tunnel_ip: str,
    server_public_key: str,
    endpoint: str,
) -> str:
    """The plaintext .conf the WireGuard app imports by QR scan.

    endpoint: "<public_ip_or_hostname>:51820"

    AllowedIPs is scoped to the server's tunnel IP only — a split tunnel. The
    phone's other traffic does not route through the house.
    """
    return "\n".join([
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {tunnel_ip}/32",
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"Endpoint = {endpoint}",
        f"AllowedIPs = {SERVER_TUNNEL_IP}/32",
        "PersistentKeepalive = 25",
        "",
    ])


def build_server_config(private_key: str, peers: list[dict]) -> str:
    """Regenerate the full server .conf from the peer list (source of truth)."""
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {SERVER_TUNNEL_IP}/24",
        f"ListenPort = {LISTEN_PORT}",
        "",
    ]
    for peer in peers:
        lines += [
            "[Peer]",
            f"# {peer.get('device_name', 'unknown')}",
            f"PublicKey = {peer['public_key']}",
            f"AllowedIPs = {peer['tunnel_ip']}/32",
            "",
        ]
    return "\n".join(lines)


def detect_public_endpoint() -> Optional[str]:
    """Best-effort public IP lookup, for pre-filling the endpoint setting.

    This is an outbound call to a third party — the only practical way to learn
    your own public IP from behind a router. Runs once, at setup.
    """
    try:
        import httpx
        ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
        # Sanity check: reject CGNAT / private ranges, which won't work anyway.
        if ip.startswith(("10.", "192.168.", "127.")):
            return None
        first, second = ip.split(".")[0], ip.split(".")[1]
        if first == "172" and 16 <= int(second) <= 31:
            return None
        if first == "100" and 64 <= int(second) <= 127:
            return None  # CGNAT — inbound is impossible
        return ip
    except Exception:
        return None
    
def build_pairing_payload(tomebox, port: int = 8000) -> tuple[str, str | None, str]:
    """Single source of truth for pairing. Used by BOTH the web endpoint and the
    desktop dialog, so they can't drift apart again.

    Returns (app_payload_json, wg_config_text_or_None, otp).
    """
    import json
    import secrets
    import socket
    import time

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

    endpoint = tomebox.settings.get("wg_endpoint", "203.12.0.79:51820")
    server_pub = get_server_public_key()
    print(f"[PAIR] endpoint={endpoint!r} server_pub={server_pub!r}")
    if endpoint and server_pub:
        peers = tomebox.settings.get("wg_peers", [])
        tunnel_ip = allocate_tunnel_ip(peers)
        priv, pub = generate_keypair()
        add_peer(pub, tunnel_ip)

        peers.append({
            "device_name": f"device-{tunnel_ip}",
            "public_key": pub,
            "tunnel_ip": tunnel_ip,
            "created": now,
        })
        tomebox.settings["wg_peers"] = []
        tomebox.db.save_settings(tomebox.settings)

        payload["vpn"] = f"http://{SERVER_TUNNEL_IP}:{port}"
        payload["wg"] = {
            "private_key": priv,
            "address": f"{tunnel_ip}/32",
            "peer_public_key": server_pub,
            "endpoint": endpoint,
            "allowed_ips": f"{SERVER_TUNNEL_IP}/32",
            "keepalive": 25,
        }
        wg_conf = build_client_config(priv, tunnel_ip, server_pub, endpoint)

    return json.dumps(payload), wg_conf, otp