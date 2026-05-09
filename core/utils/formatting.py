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