"""
Gabriel — Main Entry Point

Starts the camera capture pipeline, filter system, and vision analyzer.
Later phases will also start the API server and alert dispatcher here.
"""

import sys
import signal
import logging
import threading

from dotenv import load_dotenv
load_dotenv()  # Load .env before importing config

import config
from capture.camera import CaptureManager
from capture.filters import FrameFilter, FilteredFrame, FrameType
from analysis.vision import VisionAnalyzer

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gabriel")


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

_stats = {
    "frames_captured": 0,
    "frames_motion": 0,
    "frames_heartbeat": 0,
    "frames_burst": 0,
    "gemini_calls": 0,
    "gemini_failures": 0,
    "lee_visible": 0,
    "concerns": 0,
}
_stats_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Frame handler — sends accepted frames to Gemini
# ---------------------------------------------------------------------------

# Global analyzer instance (initialized in main)
_analyzer: VisionAnalyzer = None


def handle_accepted_frame(filtered: FilteredFrame):
    """
    Called every time a frame passes the filter pipeline.
    Sends the frame to Gemini for analysis and logs the result.
    """
    ft = filtered.frame_type.name.lower()

    with _stats_lock:
        _stats["frames_captured"] += 1
        _stats[f"frames_{ft}"] = _stats.get(f"frames_{ft}", 0) + 1

    logger.info(
        f"[{filtered.frame.camera_name}] Frame accepted: "
        f"type={ft}  motion={filtered.motion_score:.1f}%"
    )

    # Send to Gemini for analysis
    result = _analyzer.analyze_frame(
        filtered.frame.image,
        camera_name=filtered.frame.camera_name,
    )

    with _stats_lock:
        _stats["gemini_calls"] += 1

    if result is None:
        with _stats_lock:
            _stats["gemini_failures"] += 1
        logger.warning(f"[{filtered.frame.camera_name}] Analysis failed, skipping")
        return

    # Track stats
    with _stats_lock:
        if result.get("lee_visible"):
            _stats["lee_visible"] += 1
        if result.get("concern_level") in ("medium", "high"):
            _stats["concerns"] += 1

    # Log the activity detail
    detail = result.get("activity_detail", "")
    if detail:
        logger.info(f"[{filtered.frame.camera_name}] → {detail}")

    # TODO: Save to SQLite (Step 3)
    # TODO: Trigger alert dispatcher on concern events (Step 5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _analyzer

    logger.info("=" * 60)
    logger.info("  Gabriel — Starting up")
    logger.info("=" * 60)

    # Validate API key
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    # Ensure storage directories exist
    config.BASE_DIR.mkdir(parents=True, exist_ok=True)
    config.FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize vision analyzer
    _analyzer = VisionAnalyzer()
    logger.info("Gemini vision analyzer ready")

    # Start cameras
    capture_manager = CaptureManager()
    capture_manager.start_all()

    # Start filter pipeline for each camera
    filter_threads = []
    for name, camera in capture_manager.cameras.items():
        filt = FrameFilter(camera)
        filt.set_callback(handle_accepted_frame)

        t = threading.Thread(
            target=filt.run,
            name=f"filter-{name}",
            daemon=True,
        )
        t.start()
        filter_threads.append(t)
        logger.info(f"Filter pipeline started for camera: {name}")

    # Graceful shutdown on Ctrl+C
    shutdown_event = threading.Event()

    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Gabriel is running. Press Ctrl+C to stop.")
    logger.info(f"Cameras: {list(capture_manager.cameras.keys())}")
    logger.info(f"Motion threshold: {config.MOTION_THRESHOLD}%")
    logger.info(f"Dedup threshold: {config.DEDUP_THRESHOLD}")
    logger.info(f"Heartbeat interval: {config.HEARTBEAT_INTERVAL}s")
    logger.info(f"Burst threshold: {config.BURST_MOTION_THRESHOLD}%")

    # Block until shutdown
    shutdown_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    capture_manager.stop_all()

    logger.info("Final stats:")
    with _stats_lock:
        for key, val in _stats.items():
            logger.info(f"  {key}: {val}")

    logger.info("Gabriel stopped. Goodbye!")


if __name__ == "__main__":
    main()
