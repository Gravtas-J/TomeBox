
import os

# Formats that are DRM-protected and cannot be streamed to a client.
# They must be converted (e.g. to M4B) before client-side playback.
DRM_FORMATS = frozenset({"AAX", "AAXC"})
DRM_EXTENSIONS = frozenset({".aax", ".aaxc"})

# Reasons returned alongside playable=False, so clients can show the right action.
NOT_PLAYABLE_DRM = "drm"                        # client shows "Convert to play"
NOT_PLAYABLE_NOT_DOWNLOADED = "not_downloaded"  # client shows "Download"


def is_drm_path(path: str) -> bool:
    """True if the file at this path is a DRM-protected container (by extension)."""
    return os.path.splitext(str(path))[1].lower() in DRM_EXTENSIONS


def local_playability(entry: dict) -> tuple[bool, str | None]:
    """Playability of a local library entry.

    Rule: a local entry is playable unless its recorded format is AAX or AAXC.
    Returns (playable, reason). reason is None when playable.
    """
    fmt = str(entry.get("format") or "").upper()
    if fmt in DRM_FORMATS:
        return False, NOT_PLAYABLE_DRM
    return True, None