import math

# Upper bound for listened_seconds in a single report. A caller with a larger
# backlog (e.g. a client that was offline) reports it in several pieces.
MAX_LISTENED_SECONDS_PER_UPDATE = 3600.0


def apply_playback_report(stats_manager, entry, listened_seconds=0.0, finished=False):
    """Applies one playback report to the stats and (for completion) the book entry.

    stats_manager     the app's StatsManager
    entry             the book's local_library dict, or None if the book is not in
                      the library (listening time still counts; finished=True is an error)
    listened_seconds  wall-clock seconds played since the caller's last report,
                      0 <= listened_seconds <= MAX_LISTENED_SECONDS_PER_UPDATE
    finished          True when playback reached the end of the book. The book is
                      marked Finished and books_finished is counted only if it was
                      not already Finished, so re-listening never double-counts.

    Returns (newly_finished, achievements_unlocked). The caller is responsible for
    persisting the entry (set _playback_dirty / save_local_db) and, when
    newly_finished is True, calling bump_library_version.

    Raises ValueError for out-of-range listened_seconds, or finished=True with no entry.
    """
    seconds = float(listened_seconds)
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_LISTENED_SECONDS_PER_UPDATE:
        raise ValueError(
            f"listened_seconds must be between 0 and {MAX_LISTENED_SECONDS_PER_UPDATE}, "
            f"got {listened_seconds!r}"
        )
    if finished and entry is None:
        raise ValueError("finished=True requires the book's library entry")

    newly_finished = bool(finished) and entry.get("read_status") != "Finished"
    if newly_finished:
        entry["read_status"] = "Finished"

    unlocked = []
    if seconds > 0:
        unlocked += stats_manager.add_stat("seconds_listened", seconds)
    if newly_finished:
        unlocked += stats_manager.add_stat("books_finished", 1)

    return newly_finished, unlocked