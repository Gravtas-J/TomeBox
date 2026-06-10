import re

try:
    from rapidfuzz import fuzz

    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


def format_series_list(raw_series):
    """
    Joins Audible-shaped series entries into a 'Title (Bk N), Title' string.

    Skips entries with no title. Omits the '(Bk N)' suffix when sequence is missing
    or empty so we never render dangling 'Title (Bk )'.
    """
    parts = []
    for s in raw_series or []:
        if not isinstance(s, dict) or not s.get("title"):
            continue
        title = s["title"]
        seq = s.get("sequence")
        parts.append(f"{title} (Bk {seq})" if seq else title)
    return ", ".join(parts)


def normalize_title(title):
    """Strips common boilerplate and normalises punctuation for comparison."""
    if not title:
        return ""

    t = title.lower()

    # Normalise fancy quotes to ASCII
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')

    # Strip Audible-style suffixes
    suffixes = [
        " (unabridged)",
        " (abridged)",
        " (audible audio edition)",
        " (audiobook)",
        ": a novel",
    ]
    for suffix in suffixes:
        if t.endswith(suffix):
            t = t[: -len(suffix)]

    # Strip "Book N" / "Volume N" / "Vol. N" series markers
    t = re.sub(r",?\s*(book|volume|vol\.?|part)\s+\d+\s*$", "", t)

    # Remove all punctuation
    t = re.sub(r"[^\w\s]", " ", t)

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t


def find_matching_cloud_item(title, cloud_items, threshold=85):
    """Returns the best-matching cloud item for a given local title, or None."""
    if not title or not cloud_items:
        return None

    target = normalize_title(title)
    if not target:
        return None

    if RAPIDFUZZ_AVAILABLE:
        best_match = None
        best_score = 0

        for item in cloud_items:
            cloud_title = normalize_title(item.get("title", ""))
            if not cloud_title:
                continue

            # Standard fuzzy match on the full title
            score = fuzz.token_set_ratio(target, cloud_title)

            # Series-aware second pass: if the file's title appears to contain
            # the series name as a prefix, strip it and try again
            raw_series = item.get("series", []) or []
            for series_entry in raw_series:
                if not isinstance(series_entry, dict):
                    continue
                series_name = normalize_title(series_entry.get("title", ""))
                if not series_name or len(series_name) < 4:
                    continue

                # If the target starts with the series name, strip it and rematch
                if target.startswith(series_name):
                    stripped_target = target[len(series_name) :].strip()
                    if stripped_target:
                        series_score = fuzz.token_set_ratio(
                            stripped_target, cloud_title
                        )
                        score = max(score, series_score)

            if score > best_score:
                best_score = score
                best_match = item

        return best_match if best_score >= threshold else None

    # Fallback: exact match only
    for item in cloud_items:
        if normalize_title(item.get("title", "")) == target:
            return item
    return None
