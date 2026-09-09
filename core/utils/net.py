import socket


def is_port_free(port, host="0.0.0.0"):
    """True if we can bind this port right now.

    Deliberately does NOT set SO_REUSEADDR: on Windows that flag lets a second
    process bind a port another process already holds, which would make an
    occupied port look free.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(preferred=8000, host="0.0.0.0", attempts=20, logger=None):
    """Returns `preferred` if it's available, otherwise the next free port above it.

    Falls back to an OS-assigned ephemeral port if the whole scan range is busy.
    """
    for offset in range(attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if is_port_free(candidate, host):
            if candidate != preferred and logger:
                logger(f"Port {preferred} is in use — falling back to {candidate}.")
            return candidate

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        candidate = probe.getsockname()[1]

    if logger:
        logger(
            f"Ports {preferred}-{preferred + attempts - 1} are all in use — "
            f"using OS-assigned port {candidate}."
        )
    return candidate

DEFAULT_SERVER_PORT = 8000


def resolve_server_port(app, default=DEFAULT_SERVER_PORT):
    """The port the companion server is on, or will be on next time it starts.

    Live value first, then the last port we successfully bound (persisted), then
    the default. Callers that build pairing URLs or WireGuard configs must use
    this rather than assuming 8000.
    """
    live = getattr(app, "server_port", None)
    if live:
        return live

    settings = getattr(app, "settings", None) or {}
    return settings.get("server_port") or default