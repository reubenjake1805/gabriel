"""
Gabriel — Configuration

All tunable parameters in one place. Copy this file to config_local.py
and fill in your API keys. config_local.py is gitignored.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
CAMERA_SOURCES = {
    "living_room": "rtsp://aj_home:gabriel3007@192.168.1.15:554/av_stream/ch0",
}

# ---------------------------------------------------------------------------
# Frame Capture
# ---------------------------------------------------------------------------
CAPTURE_INTERVAL = 1.0          # seconds between raw frame grabs
RING_BUFFER_SECONDS = 30        # seconds of frames kept in memory for concern context

# ---------------------------------------------------------------------------
# Filter Pipeline
# ---------------------------------------------------------------------------
MOTION_THRESHOLD = 2.0          # % of pixels changed to trigger motion
MOTION_BLUR_KERNEL = 21         # Gaussian blur kernel size (reduces noise)
MOTION_BINARY_THRESH = 25       # pixel intensity threshold for binary diff
DEDUP_THRESHOLD = 12            # pHash Hamming distance threshold
HEARTBEAT_INTERVAL = 900        # seconds (15 min) between forced samples

# ---------------------------------------------------------------------------
# Burst Mode
# ---------------------------------------------------------------------------
BURST_MOTION_THRESHOLD = 15.0   # % of pixels changed to trigger burst mode
BURST_FPS = 4                   # capture rate during burst mode (normal: 1 fps)
BURST_DURATION_SECONDS = 5      # how long burst mode lasts
BURST_ANALYZE_COUNT = 6         # burst frames sent to Gemini (rest saved to disk only)

# ---------------------------------------------------------------------------
# Vision API (Gemini)
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
FRAME_JPEG_QUALITY = 85         # JPEG quality for frames sent to API

# ---------------------------------------------------------------------------
# Chat API (Claude)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
SESSION_GAP = 10                # minutes — events closer than this are one session

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
ALERT_ENABLED = True
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
INACTIVITY_ALERT_HOURS = 4      # alert if Lee not seen for this long
ALERT_COOLDOWN_MINUTES = 5      # minimum gap between alerts

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
BASE_DIR = Path.home() / "gabriel"
DB_PATH = BASE_DIR / "gabriel.db"
FRAMES_DIR = BASE_DIR / "frames"
FRAME_RETENTION_HOURS = 48      # auto-delete frames older than this

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
API_HOST = "0.0.0.0"
API_PORT = 8080

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
LOCAL_TIMEZONE = "Asia/Kolkata"  # IST (UTC+5:30)

# ---------------------------------------------------------------------------
# Override with local config if it exists
# ---------------------------------------------------------------------------
try:
    from config_local import *  # noqa: F401, F403
except ImportError:
    pass
