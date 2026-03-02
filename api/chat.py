"""
Gabriel — Claude Chat Endpoint

Takes a natural-language question, pulls relevant events from SQLite,
aggregates them into sessions, and sends them to Claude for a
warm conversational answer.
"""

import logging
from datetime import datetime, timezone, timedelta

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

        # Collect frame paths from sessions
        frames = []
        for session in sessions:
            for frame_path in session.get("frames", []):
                if frame_path:
                    frames.append({
                        "timestamp": session["start_time"],
                        "url": frame_path,
                        "activity": session["activity"],
                    })

        # Build the prompt for Claude
        now = datetime.now(timezone.utc)
        time_context = now.strftime("%A, %B %d, %Y at %-I:%M %p UTC")

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

        return {
            "answer": answer,
            "events_used": len(events),
            "frames": frames[:10],  # cap at 10 frames
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
