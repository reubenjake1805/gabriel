"""
Gabriel — SQLite Event Log

Stores all analyzed events in a queryable SQLite database.
Each row represents one frame that was analyzed by Gemini.
"""

import sqlite3
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class EventDB:
    """
    Thread-safe SQLite event log.

    All writes go through a single connection with a lock to avoid
    SQLite's "database is locked" errors from concurrent threads.
    """

    def __init__(self, db_path: Path = None):
        self._db_path = str(db_path or config.DB_PATH)
        self._lock = threading.Lock()
        self._conn = None
        self._init_db()

    def _init_db(self):
        """Create the database and tables if they don't exist."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # access columns by name
        self._conn.execute("PRAGMA journal_mode=WAL")  # better concurrent reads
        self._conn.execute("PRAGMA busy_timeout=5000")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                camera TEXT NOT NULL,
                frame_type TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'vision',
                lee_visible INTEGER NOT NULL,
                lee_location TEXT,
                activity TEXT NOT NULL,
                activity_detail TEXT,
                posture TEXT,
                energy_level TEXT,
                concern_level TEXT NOT NULL DEFAULT 'none',
                concern_detail TEXT,
                environment_notes TEXT,
                frame_path TEXT,
                context_frames_dir TEXT,
                raw_response TEXT,
                analysis_time REAL,
                motion_score REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_activity
                ON events(activity);
            CREATE INDEX IF NOT EXISTS idx_events_concern
                ON events(concern_level);
            CREATE INDEX IF NOT EXISTS idx_events_source
                ON events(source);
        """)

        self._conn.commit()
        logger.info(f"Database initialized: {self._db_path}")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def insert_event(
        self,
        timestamp: datetime,
        camera: str,
        frame_type: str,
        analysis: dict,
        frame_path: str = None,
        context_frames_dir: str = None,
        motion_score: float = None,
    ) -> int:
        """
        Insert a new event into the database.

        Args:
            timestamp: When the frame was captured (UTC).
            camera: Camera name (e.g. "webcam").
            frame_type: "motion", "heartbeat", or "burst".
            analysis: The parsed JSON dict from Gemini.
            frame_path: Path to the saved JPEG on disk.
            context_frames_dir: Path to ring buffer dump (concern events only).
            motion_score: Percentage of pixels changed.

        Returns:
            The row ID of the inserted event.
        """
        ts_str = timestamp.isoformat()

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO events (
                    timestamp, camera, frame_type, source,
                    lee_visible, lee_location, activity, activity_detail,
                    posture, energy_level, concern_level, concern_detail,
                    environment_notes, frame_path, context_frames_dir,
                    raw_response, analysis_time, motion_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_str,
                    camera,
                    frame_type,
                    "vision",
                    1 if analysis.get("lee_visible") else 0,
                    analysis.get("lee_location"),
                    analysis.get("activity", "unknown"),
                    analysis.get("activity_detail"),
                    analysis.get("posture"),
                    analysis.get("energy_level"),
                    analysis.get("concern_level", "none"),
                    analysis.get("concern_detail"),
                    analysis.get("environment_notes"),
                    frame_path,
                    context_frames_dir,
                    analysis.get("_raw_response"),
                    analysis.get("_analysis_time"),
                    motion_score,
                ),
            )
            self._conn.commit()
            row_id = cursor.lastrowid

        logger.debug(f"Event #{row_id} inserted: {analysis.get('activity')}")
        return row_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_events(
        self,
        since: str = None,
        until: str = None,
        activity: str = None,
        concern_level: str = None,
        camera: str = None,
        lee_visible: bool = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        Query events with optional filters.

        Args:
            since: ISO 8601 timestamp — only events after this time.
            until: ISO 8601 timestamp — only events before this time.
            activity: Filter by activity type (e.g. "eating").
            concern_level: Filter by concern level (e.g. "high").
            camera: Filter by camera name.
            lee_visible: Filter by whether Lee was visible.
            limit: Max rows to return.

        Returns:
            List of event dicts, ordered by timestamp ascending.
        """
        conditions = []
        params = []

        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)
        if activity:
            conditions.append("activity = ?")
            params.append(activity)
        if concern_level:
            conditions.append("concern_level = ?")
            params.append(concern_level)
        if camera:
            conditions.append("camera = ?")
            params.append(camera)
        if lee_visible is not None:
            conditions.append("lee_visible = ?")
            params.append(1 if lee_visible else 0)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT * FROM events
            {where_clause}
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        return [dict(row) for row in rows]

    def get_latest_event(self, camera: str = None) -> dict | None:
        """Get the most recent event, optionally filtered by camera."""
        conditions = []
        params = []

        if camera:
            conditions.append("camera = ?")
            params.append(camera)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT * FROM events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT 1
        """

        with self._lock:
            row = self._conn.execute(query, params).fetchone()

        return dict(row) if row else None

    def get_today_summary(self) -> dict:
        """
        Get a quick summary of today's events.
        Useful for the /api/status endpoint.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) as total_events,
                    SUM(CASE WHEN lee_visible = 1 THEN 1 ELSE 0 END) as lee_visible_count,
                    SUM(CASE WHEN activity = 'eating' THEN 1 ELSE 0 END) as eating_events,
                    SUM(CASE WHEN activity = 'sleeping' OR activity = 'resting' THEN 1 ELSE 0 END) as resting_events,
                    SUM(CASE WHEN concern_level IN ('medium', 'high') THEN 1 ELSE 0 END) as concern_events
                FROM events
                WHERE timestamp >= ?
                """,
                (today,),
            ).fetchone()

        return dict(row) if row else {}

    def get_event_count(self) -> int:
        """Total number of events in the database."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            logger.info("Database connection closed")
