"""
Gabriel — Session Aggregation

Collapses consecutive same-activity events into sessions before
sending them to Claude. This prevents "Lee groomed 7 times"
when it was really one 18-minute grooming session.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)


def aggregate_sessions(events: list[dict], gap_minutes: int = None) -> list[dict]:
    """
    Collapse consecutive events with the same activity into sessions.

    Two events are merged into the same session if:
    1. They have the same activity
    2. The gap between them is less than gap_minutes

    Args:
        events: List of event dicts from the database, ordered by timestamp ASC.
        gap_minutes: Max gap between events to consider them one session.
                     Defaults to config.SESSION_GAP.

    Returns:
        List of session dicts, each containing:
        - start_time: ISO timestamp of the first event
        - end_time: ISO timestamp of the last event (same as start if single event)
        - activity: the activity type
        - event_count: how many raw events were merged
        - duration_minutes: approximate duration of the session
        - details: concatenated unique activity_detail values
        - concern_level: highest concern level in the session
        - lee_visible: True if Lee was visible in any event
        - frames: list of frame paths from the events
    """
    if not events:
        return []

    gap = gap_minutes or config.SESSION_GAP
    sessions = []
    current_session = None

    for event in events:
        ts = event.get("timestamp", "")
        activity = event.get("activity", "unknown")

        if current_session is None:
            # Start a new session
            current_session = _new_session(event)
            continue

        # Check if this event should merge into the current session
        time_gap = _minutes_between(current_session["end_time"], ts)
        same_activity = activity == current_session["activity"]

        if same_activity and time_gap <= gap:
            # Merge into current session
            _merge_into_session(current_session, event)
        else:
            # Finalize current session and start a new one
            sessions.append(_finalize_session(current_session))
            current_session = _new_session(event)

    # Don't forget the last session
    if current_session:
        sessions.append(_finalize_session(current_session))

    return sessions


def sessions_to_prompt(sessions: list[dict]) -> str:
    """
    Convert aggregated sessions into a text summary suitable for
    including in a Claude prompt.

    Example output:
        [09:14 AM] eating (1 event) — Lee ate from the automatic feeder, bowl appeared half full.
        [11:00 AM – 01:30 PM] sleeping (12 events, ~2.5 hrs) — Lee slept on the couch, curled up.
        [01:35 PM] eating (1 event) — Lee ate from the feeder again.
        [02:00 PM – 02:18 PM] grooming (5 events, ~18 min) — Front paws, side, back leg, chest, tail.
    """
    if not sessions:
        return "No events recorded for this time period."

    lines = []
    for s in sessions:
        # Format time
        start = _format_time(s["start_time"])
        if s["duration_minutes"] > 0:
            end = _format_time(s["end_time"])
            time_str = f"[{start} – {end}]"
        else:
            time_str = f"[{start}]"

        # Format duration
        count = s["event_count"]
        dur = s["duration_minutes"]
        if dur >= 60:
            dur_str = f"~{dur / 60:.1f} hrs"
        elif dur > 0:
            dur_str = f"~{dur:.0f} min"
        else:
            dur_str = ""

        # Build the count/duration part
        meta_parts = [f"{count} event{'s' if count > 1 else ''}"]
        if dur_str:
            meta_parts.append(dur_str)
        meta = ", ".join(meta_parts)

        # Concern flag
        concern = s["concern_level"]
        concern_flag = ""
        if concern == "high":
            concern_flag = " ⚠️ HIGH CONCERN"
        elif concern == "medium":
            concern_flag = " ⚠️ CONCERN"

        # Details
        details = s["details"]
        if len(details) > 200:
            details = details[:197] + "..."

        line = f"{time_str} {s['activity']} ({meta}){concern_flag} — {details}"
        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_session(event: dict) -> dict:
    """Create a new session from a single event."""
    return {
        "start_time": event.get("timestamp", ""),
        "end_time": event.get("timestamp", ""),
        "activity": event.get("activity", "unknown"),
        "event_count": 1,
        "details_list": [event.get("activity_detail", "")],
        "concern_levels": [event.get("concern_level", "none")],
        "lee_visible": bool(event.get("lee_visible")),
        "frames": [event.get("frame_path")] if event.get("frame_path") else [],
    }


def _merge_into_session(session: dict, event: dict):
    """Merge an event into an existing session."""
    session["end_time"] = event.get("timestamp", session["end_time"])
    session["event_count"] += 1

    detail = event.get("activity_detail", "")
    if detail and detail not in session["details_list"]:
        session["details_list"].append(detail)

    session["concern_levels"].append(event.get("concern_level", "none"))

    if event.get("lee_visible"):
        session["lee_visible"] = True

    if event.get("frame_path"):
        session["frames"].append(event["frame_path"])


def _finalize_session(session: dict) -> dict:
    """Finalize a session: compute duration, pick highest concern, join details."""
    duration = _minutes_between(session["start_time"], session["end_time"])

    # Pick the highest concern level
    concern_priority = {"none": 0, "low": 1, "medium": 2, "high": 3}
    highest_concern = max(
        session["concern_levels"],
        key=lambda c: concern_priority.get(c, 0),
    )

    # Join unique details
    details = ". ".join(
        d for d in session["details_list"] if d
    )
    if not details:
        details = session["activity"]

    return {
        "start_time": session["start_time"],
        "end_time": session["end_time"],
        "activity": session["activity"],
        "event_count": session["event_count"],
        "duration_minutes": round(duration, 1),
        "details": details,
        "concern_level": highest_concern,
        "lee_visible": session["lee_visible"],
        "frames": session["frames"],
    }


def _minutes_between(ts1: str, ts2: str) -> float:
    """Calculate minutes between two ISO timestamps."""
    try:
        t1 = datetime.fromisoformat(ts1)
        t2 = datetime.fromisoformat(ts2)
        return abs((t2 - t1).total_seconds()) / 60
    except (ValueError, TypeError):
        return 0.0


def _format_time(iso_timestamp: str) -> str:
    """Format an ISO timestamp as a readable local time like '2:30 AM'."""
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        local_tz = ZoneInfo(config.LOCAL_TIMEZONE)
        dt_local = dt.astimezone(local_tz)
        return dt_local.strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp
