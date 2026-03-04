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

    def __init__(self, db: EventDB):
        self._db = db
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

        # Pull events from the database
        events = self._db.get_events(since=since, until=until, limit=500)

        # Aggregate into sessions
        sessions = aggregate_sessions(events)
        event_summary = sessions_to_prompt(sessions)

        # Build the prompt for Claude
        local_tz = ZoneInfo(config.LOCAL_TIMEZONE)
        now = datetime.now(local_tz)
        time_context = now.strftime("%A, %B %d, %Y at %-I:%M %p IST")

        user_message = f"""Current time: {time_context}

The user asks: "{question}"

Here is the event log for Lee (aggregated into activity sessions):

{event_summary}

Please answer the user's question based on this data."""

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

    def _pick_relevant_frames(
        self,
        sessions: list[dict],
        question: str,
        answer: str,
        is_realtime: bool,
    ) -> list[dict]:
        """
        Pick frames that are relevant to what Claude actually talked about.

        Strategy:
        1. For realtime queries: only show the 1-2 most recent frames
        2. For activity-specific queries: show frames matching that activity
        3. For general queries: show diverse frames from different activities
           mentioned in Claude's answer
        """
        if not sessions:
            return []

        # For "right now" questions, just show the latest 2 frames
        if is_realtime:
            return self._get_latest_frames(sessions, count=2)

        # Detect which activities Claude mentioned in the answer
        answer_lower = answer.lower()
        activity_keywords = {
            "eating": ["eat", "ate", "eating", "food", "meal", "feeder", "bowl"],
            "drinking": ["drink", "drinking", "water"],
            "sleeping": ["sleep", "sleeping", "asleep", "nap", "napping", "snooze"],
            "resting": ["rest", "resting", "lying", "relaxing", "cozy"],
            "playing": ["play", "playing", "chasing", "toy"],
            "grooming": ["groom", "grooming", "licking", "cleaning"],
            "exploring": ["explor", "walking", "roaming", "wander"],
            "climbing": ["climb", "climbing", "jumped", "perch"],
            "running": ["run", "running", "sprint", "zoom"],
            "using_litter_box": ["litter", "bathroom"],
            "looking_outside": ["window", "outside", "looking out"],
        }

        # Find which activities Claude mentioned
        mentioned_activities = set()
        for activity, keywords in activity_keywords.items():
            if any(kw in answer_lower for kw in keywords):
                mentioned_activities.add(activity)

        # Also check the question for activity hints
        q_lower = question.lower()
        for activity, keywords in activity_keywords.items():
            if any(kw in q_lower for kw in keywords):
                mentioned_activities.add(activity)

        # Pick frames from mentioned activities
        frames = []
        if mentioned_activities:
            for session in reversed(sessions):
                if session["activity"] in mentioned_activities:
                    frame = self._get_session_frame(session)
                    if frame:
                        frames.append(frame)
                if len(frames) >= 4:
                    break

        # If we didn't find enough activity-specific frames, fill with recent ones
        if len(frames) < 2:
            recent = self._get_latest_frames(sessions, count=4)
            # Add recent frames that aren't already included
            existing_urls = {f["url"] for f in frames}
            for f in recent:
                if f["url"] not in existing_urls:
                    frames.append(f)
                if len(frames) >= 4:
                    break

        return frames[:6]

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
