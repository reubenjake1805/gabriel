"""
Gabriel — Main Entry Point

Starts the camera capture pipeline and filter system.
Later phases will also start the API server and alert dispatcher here.
"""

import sys
import signal
import logging
import threading

import config
from capture.camera import CaptureManager
from capture.filters import FrameFilter, FilteredFrame

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
# Frame handler (placeholder — will be replaced by Gemini analyzer)
# ---------------------------------------------------------------------------

_frame_counter = {"total": 0, "motion": 0, "heartbeat": 0, "burst": 0}


def handle_accepted_frame(filtered: FilteredFrame):
    """
    Called every time a frame passes the filter pipeline.

    Phase 1: Just log it so we can verify the pipeline is working.
    Phase 2: This will send the frame to the Gemini vision analyzer.
    """
    ft = filtered.frame_type.name.lower()
    _frame_counter["total"] += 1
    _frame_counter[ft] = _frame_counter.get(ft, 0) + 1

    logger.info(
        f"[{filtered.frame.camera_name}] Frame accepted: "
        f"type={ft}  motion={filtered.motion_score:.1f}%  "
        f"total={_frame_counter['total']}  "
        f"(motion={_frame_counter['motion']} "
        f"hb={_frame_counter['heartbeat']} "
        f"burst={_frame_counter['burst']})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("  Gabriel — Starting up")
    logger.info("=" * 60)

    # Ensure storage directories exist
    config.BASE_DIR.mkdir(parents=True, exist_ok=True)
    config.FRAMES_DIR.mkdir(parents=True, exist_ok=True)

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
    for key, val in _frame_counter.items():
        logger.info(f"  {key}: {val}")

    logger.info("Gabriel stopped. Goodbye!")


if __name__ == "__main__":
    main()
