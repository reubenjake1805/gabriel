"""
Gabriel — Claude Chat Endpoint

Takes a natural-language question, pulls relevant events from SQLite,
aggregates them into sessions, and sends them to Claude for a
warm conversational answer.
"""

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import anthropic

import config
from storage.database import EventDB
from api.sessions import aggregate_sessions, sessions_to_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for Claude
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """You are Gabriel, a friendly and reassuring AI assistant that helps monitor
a 10-month-old cat named Rock Lee (called "Lee"). You answer questions from
Lee's owners based on the event log data provided.

Guidelines:
- Be warm and conversational, not clinical.
- When Lee is doing well (which is most of the time), be reassuring.
- If there are any concerns in the log, mention them clearly but calmly.
- If you don't have enough data to answer confidently, say so honestly.
- Refer to specific times when relevant ("Lee ate around 9:15 AM").
- Never make up events that aren't in the log.
- Keep answers concise — 2-4 sentences for simple questions, more for detailed ones.
- If concern events exist, always mention them even if the question didn't ask about concerns."""


# ---------------------------------------------------------------------------
# Chat handler
# ---------------------------------------------------------------------------

class ChatHandler:
    """Handles natural-language questions about Lee using Claude."""

    def __init__(self, db: EventDB, capture_manager=None, analyzer=None, frame_store=None):
        self._db = db
        self._capture_manager = capture_manager
        self._analyzer = analyzer
        self._frame_store = frame_store
        self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        logger.info("Chat handler initialized")

    def ask(self, question: str) -> dict:
        """
        Answer a question about Lee.

        Args:
            question: Natural-language question (e.g. "Has Lee eaten today?")

        Returns:
            Dict with:
            - answer: Claude's response text
            - events_used: number of events included in context
            - frames: list of frame info dicts for the relevant events
            - is_realtime: whether this was a "right now" query
        """
        # Determine time range based on the question
        since, until = self._infer_time_range(question)
        is_realtime = self._is_realtime_query(question)

        # For realtime queries, grab fresh frames from all cameras
        live_snapshots = []
        if is_realtime and self._capture_manager and self._analyzer:
            live_snapshots = self._capture_live_snapshots()

        # Pull events from the database
        events = self._db.get_events(since=since, until=until, limit=500)

        # Aggregate into sessions
        sessions = aggregate_sessions(events)
        event_summary = sessions_to_prompt(sessions)

        # Add live snapshot info to the prompt
        live_summary = ""
        if live_snapshots:
            live_lines = []
            for snap in live_snapshots:
                if snap["analysis"]:
                    a = snap["analysis"]
                    visible = "visible" if a.get("lee_visible") else "not visible"
                    activity = a.get("activity", "unknown")
                    detail = a.get("activity_detail", "")
                    concern = a.get("concern_level", "none")
                    live_lines.append(
                        f"  [{snap['camera']}] Lee is {visible}. "
                        f"Activity: {activity}. {detail} "
                        f"(concern: {concern})"
                    )
                else:
                    live_lines.append(f"  [{snap['camera']}] Analysis failed.")

            live_summary = (
                "\n\nLIVE SNAPSHOTS (just captured moments ago):\n"
                + "\n".join(live_lines)
            )

        # Build the prompt for Claude
        local_tz = ZoneInfo(config.LOCAL_TIMEZONE)
        now = datetime.now(local_tz)
        time_context = now.strftime("%A, %B %d, %Y at %-I:%M %p IST")

        user_message = f"""Current time: {time_context}

The user asks: "{question}"

Here is the event log for Lee (aggregated into activity sessions):

{event_summary}{live_summary}

Please answer the user's question based on this data. If live snapshots are provided, prioritize that information for "right now" questions."""

        # Call Claude
        try:
            response = self._client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=500,
                system=CHAT_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_message}
                ],
            )
            answer = response.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            answer = (
                "I'm sorry, I'm having trouble connecting to my brain right now. "
                "Please try again in a moment."
            )

        # Pick frames relevant to the answer
        frames = self._pick_relevant_frames(sessions, question, answer, is_realtime)

        # For realtime queries, prepend live snapshot frames
        if live_snapshots:
            live_frames = []
            for snap in live_snapshots:
                if snap.get("frame_path"):
                    live_frames.append({
                        "timestamp": snap["timestamp"],
                        "url": snap["frame_path"],
                        "activity": snap["analysis"].get("activity", "unknown") if snap["analysis"] else "unknown",
                    })
            # Live frames first, then historical
            frames = live_frames + [f for f in frames if f["url"] not in {lf["url"] for lf in live_frames}]
            frames = frames[:6]

        return {
            "answer": answer,
            "events_used": len(events),
            "frames": frames,
            "is_realtime": is_realtime,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_time_range(self, question: str) -> tuple[str, str | None]:
        """
        Guess the relevant time range from the question.
        Returns (since, until) as ISO timestamp strings.
        """
        q = question.lower()
        now = datetime.now(timezone.utc)

        # "today" / default — from midnight today
        if any(word in q for word in ["today", "this morning", "this afternoon", "this evening"]):
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return since.isoformat(), None

        # "last hour", "past hour"
        if "last hour" in q or "past hour" in q:
            since = now - timedelta(hours=1)
            return since.isoformat(), None

        # "last X hours"
        for hours in [2, 3, 4, 6, 8, 12]:
            if f"last {hours} hour" in q or f"past {hours} hour" in q:
                since = now - timedelta(hours=hours)
                return since.isoformat(), None

        # "yesterday"
        if "yesterday" in q:
            yesterday = now - timedelta(days=1)
            since = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            until = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return since.isoformat(), until.isoformat()

        # "this week"
        if "this week" in q or "past week" in q or "last week" in q:
            since = now - timedelta(days=7)
            return since.isoformat(), None

        # "right now", "currently" — last 30 minutes
        if self._is_realtime_query(question):
            since = now - timedelta(minutes=30)
            return since.isoformat(), None

        # Default: today
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return since.isoformat(), None

    def _is_realtime_query(self, question: str) -> bool:
        """Check if the question is asking about Lee's current state."""
        q = question.lower()
        realtime_keywords = [
            "right now", "at the moment", "currently", "doing now",
            "what is lee doing", "what's lee doing", "where is lee",
            "is lee okay", "is lee ok", "how is lee",
        ]
        return any(kw in q for kw in realtime_keywords)

    def _capture_live_snapshots(self) -> list[dict]:
        """
        Grab a fresh frame from each camera, analyze it with Gemini,
        save it to disk and database. Returns list of snapshot dicts.
        """
        snapshots = []

        for camera_name, camera in self._capture_manager.cameras.items():
            if not camera.is_running:
                continue

            frame = camera.get_latest_frame()
            if frame is None:
                logger.warning(f"[{camera_name}] No frame available for live snapshot")
                continue

            logger.info(f"[{camera_name}] Capturing live snapshot for chat query")

            # Analyze with Gemini
            analysis = self._analyzer.analyze_frame(
                frame.image,
                camera_name=camera_name,
            )

            # Save frame to disk
            frame_path = None
            if self._frame_store:
                frame_path = self._frame_store.save_frame(
                    image=frame.image,
                    camera_name=camera_name,
                    timestamp=frame.timestamp,
                    frame_type="live",
                )

            # Store in database
            if analysis:
                self._db.insert_event(
                    timestamp=frame.timestamp,
                    camera=camera_name,
                    frame_type="live",
                    analysis=analysis,
                    frame_path=frame_path,
                    motion_score=0.0,
                )

            snapshots.append({
                "camera": camera_name,
                "timestamp": frame.timestamp.isoformat(),
                "analysis": analysis,
                "frame_path": frame_path,
            })

        return snapshots

    def _pick_relevant_frames(
        self,
        sessions: list[dict],
        question: str,
        answer: str,
        is_realtime: bool,
    ) -> list[dict]:
        """
        Pick frames that are relevant to the user's question.

        Strategy:
        1. For realtime queries: only show the 1-2 most recent frames
        2. If the question asks about a specific activity (eating, sleeping, etc),
           ONLY show frames from that activity
        3. For general questions: show diverse recent frames where Lee is visible
        """
        if not sessions:
            return []

        # For "right now" questions, just show the latest 2 frames
        if is_realtime:
            return self._get_latest_frames(sessions, count=2)

        activity_keywords = {
            "eating": ["eat", "ate", "eating", "food", "meal", "feeder", "bowl", "dinner", "lunch", "breakfast", "fed"],
            "drinking": ["drink", "drinking", "water", "thirsty"],
            "sleeping": ["sleep", "sleeping", "asleep", "nap", "napping", "snooze", "slept"],
            "resting": ["rest", "resting", "lying", "relaxing", "cozy", "cuddle"],
            "playing": ["play", "playing", "chasing", "toy", "played"],
            "grooming": ["groom", "grooming", "licking", "cleaning", "groomed"],
            "exploring": ["explor", "walking", "roaming", "wander", "walked"],
            "climbing": ["climb", "climbing", "jumped", "perch"],
            "running": ["run", "running", "sprint", "zoom", "ran"],
            "using_litter_box": ["litter", "bathroom", "poop", "pee"],
            "looking_outside": ["window", "outside", "looking out"],
        }

        # First: check the QUESTION for a specific activity
        q_lower = question.lower()
        question_activities = set()
        for activity, keywords in activity_keywords.items():
            if any(kw in q_lower for kw in keywords):
                question_activities.add(activity)

        # If the question is about a specific activity, ONLY show those frames
        if question_activities:
            frames = []
            for session in reversed(sessions):
                if session["activity"] in question_activities:
                    frame = self._get_session_frame(session)
                    if frame:
                        frames.append(frame)
                if len(frames) >= 4:
                    break
            if frames:
                return frames[:4]

        # General question: show diverse recent frames where Lee is visible
        return self._get_latest_frames(sessions, count=4)

    def _get_latest_frames(self, sessions: list[dict], count: int) -> list[dict]:
        """Get the N most recent frames where Lee is visible."""
        frames = []
        for session in reversed(sessions):
            if session.get("lee_visible") and session["activity"] != "not_visible":
                frame = self._get_session_frame(session)
                if frame:
                    frames.append(frame)
            if len(frames) >= count:
                break
        return frames

    def _get_session_frame(self, session: dict) -> dict | None:
        """Get the best frame from a session (the last one)."""
        session_frames = session.get("frames", [])
        if not session_frames:
            return None
        last_frame = session_frames[-1]
        if not last_frame:
            return None
        return {
            "timestamp": session["end_time"],
            "url": last_frame,
            "activity": session["activity"],
        }
