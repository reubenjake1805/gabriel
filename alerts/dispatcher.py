"""
Gabriel — Alert Dispatcher

Sends proactive Telegram notifications when:
1. A concern event is detected (medium/high)
2. Lee hasn't been seen for too long (inactivity alert)
"""

import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests

import config
from storage.database import EventDB

logger = logging.getLogger(__name__)


class AlertDispatcher:
    """
    Watches for concern events and inactivity, sends Telegram alerts.
    """

    def __init__(self, db: EventDB):
        self._db = db
        self._last_alert_time = 0.0  # monotonic clock
        self._bot_url = (
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        )
        self._enabled = (
            config.ALERT_ENABLED
            and bool(config.TELEGRAM_BOT_TOKEN)
            and bool(config.TELEGRAM_CHAT_ID)
        )

        if self._enabled:
            logger.info("Alert dispatcher initialized (Telegram)")
        else:
            logger.warning(
                "Alert dispatcher disabled — missing TELEGRAM_BOT_TOKEN "
                "or TELEGRAM_CHAT_ID in .env"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_concern_event(self, analysis: dict, camera: str, timestamp: datetime):
        """
        Called when Gemini flags a concern event.
        Sends a Telegram alert if cooldown has elapsed.
        """
        if not self._enabled:
            return

        concern = analysis.get("concern_level", "none")
        if concern not in ("medium", "high"):
            return

        # Check cooldown
        now = time.monotonic()
        cooldown_sec = config.ALERT_COOLDOWN_MINUTES * 60
        if now - self._last_alert_time < cooldown_sec:
            logger.info("Alert suppressed (cooldown active)")
            return

        self._last_alert_time = now

        # Format the alert message
        local_tz = ZoneInfo(config.LOCAL_TIMEZONE)
        local_time = timestamp.astimezone(local_tz)
        time_str = local_time.strftime("%-I:%M %p")

        activity = analysis.get("activity", "unknown")
        detail = analysis.get("activity_detail", "")
        concern_detail = analysis.get("concern_detail", "")

        if concern == "high":
            emoji = "🚨"
            level = "HIGH CONCERN"
        else:
            emoji = "⚠️"
            level = "CONCERN"

        message = (
            f"{emoji} *Gabriel Alert — {level}*\n\n"
            f"*Time:* {time_str}\n"
            f"*Camera:* {camera}\n"
            f"*Activity:* {activity}\n"
        )
        if detail:
            message += f"*Detail:* {detail}\n"
        if concern_detail:
            message += f"*Concern:* {concern_detail}\n"
        message += "\nPlease check the app for more details."

        self._send_telegram(message)

    def start_inactivity_monitor(self, shutdown_event: threading.Event):
        """
        Runs in a background thread. Periodically checks if Lee
        hasn't been seen for too long and sends a gentle alert.
        """
        if not self._enabled:
            return

        logger.info(
            f"Inactivity monitor started "
            f"(threshold: {config.INACTIVITY_ALERT_HOURS} hours)"
        )

        # Check every 30 minutes
        check_interval = 30 * 60
        last_inactivity_alert = 0.0

        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=check_interval)
            if shutdown_event.is_set():
                break

            try:
                self._check_inactivity(last_inactivity_alert)
            except Exception as e:
                logger.error(f"Inactivity check error: {e}")

    def _check_inactivity(self, last_inactivity_alert: float):
        """Check if Lee hasn't been seen recently."""
        threshold = timedelta(hours=config.INACTIVITY_ALERT_HOURS)
        cutoff = (datetime.now(timezone.utc) - threshold).isoformat()

        # Look for any event where Lee was visible since the cutoff
        recent_visible = self._db.get_events(
            since=cutoff,
            lee_visible=True,
            limit=1,
        )

        if not recent_visible:
            # Check cooldown for inactivity alerts (use 1 hour cooldown)
            now = time.monotonic()
            if now - last_inactivity_alert < 3600:
                return

            local_tz = ZoneInfo(config.LOCAL_TIMEZONE)
            now_local = datetime.now(local_tz)
            time_str = now_local.strftime("%-I:%M %p")

            message = (
                f"🐱 *Gabriel*\n\n"
                f"I haven't seen Lee in about "
                f"{config.INACTIVITY_ALERT_HOURS} hours "
                f"(as of {time_str}). He might be in a spot the camera "
                f"can't see, but you may want to check in."
            )
            self._send_telegram(message)

    # ------------------------------------------------------------------
    # Telegram API
    # ------------------------------------------------------------------

    def _send_telegram(self, message: str):
        """Send a message to the configured Telegram chat."""
        try:
            response = requests.post(
                f"{self._bot_url}/sendMessage",
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )

            if response.status_code == 200:
                logger.info("Telegram alert sent successfully")
            else:
                logger.error(
                    f"Telegram API error: {response.status_code} "
                    f"{response.text}"
                )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    def send_test_alert(self):
        """Send a test message to verify Telegram is working."""
        if not self._enabled:
            logger.warning("Cannot send test alert — dispatcher disabled")
            return False

        self._send_telegram(
            "🐱 *Gabriel Test Alert*\n\n"
            "If you're seeing this, Telegram alerts are working! "
            "Gabriel will notify you here if anything concerning "
            "happens with Lee."
        )
        return True
