"""
Gabriel — Main Entry Point

Starts the camera capture pipeline, filter system, vision analyzer,
SQLite storage, alert dispatcher, and API server.
"""

import sys
import signal
import logging
import threading
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # Load .env before importing config

import uvicorn

import config
from capture.camera import CaptureManager
from capture.filters import FrameFilter, FilteredFrame, FrameType
from analysis.vision import VisionAnalyzer
from storage.database import EventDB
from storage.frames import FrameStore
from alerts.dispatcher import AlertDispatcher
from audio.capture import AudioManager
from api.server import create_app

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
    "events_stored": 0,
    "lee_visible": 0,
    "concerns": 0,
}
_stats_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Global instances (initialized in main)
# ---------------------------------------------------------------------------

_analyzer: VisionAnalyzer = None
_db: EventDB = None
_frame_store: FrameStore = None
_capture_manager: CaptureManager = None
_alert_dispatcher: AlertDispatcher = None
_audio_manager: AudioManager = None


# ---------------------------------------------------------------------------
# Frame handler — analyze, save, store, and alert
# ---------------------------------------------------------------------------

def handle_accepted_frame(filtered: FilteredFrame):
    """
    Called every time a frame passes the filter pipeline.
    1. Send frame to Gemini for analysis
    2. Save frame to disk
    3. Store event in SQLite
    4. If concern event, flush ring buffer + send alert
    """
    ft = filtered.frame_type.name.lower()

    with _stats_lock:
        _stats["frames_captured"] += 1
        _stats[f"frames_{ft}"] = _stats.get(f"frames_{ft}", 0) + 1

    logger.info(
        f"[{filtered.frame.camera_name}] Frame accepted: "
        f"type={ft}  motion={filtered.motion_score:.1f}%"
    )

    # 1. Send to Gemini for analysis
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

    # Log the activity detail
    detail = result.get("activity_detail", "")
    if detail:
        logger.info(f"[{filtered.frame.camera_name}] → {detail}")

    # 2. Save frame to disk
    frame_path = _frame_store.save_frame(
        image=filtered.frame.image,
        camera_name=filtered.frame.camera_name,
        timestamp=filtered.frame.timestamp,
        frame_type=ft,
    )

    # 3. Check for concern event → flush ring buffer + alert
    context_frames_dir = None
    concern = result.get("concern_level", "none")
    if concern in ("medium", "high"):
        with _stats_lock:
            _stats["concerns"] += 1

        # Flush ring buffer
        camera = _capture_manager.get_camera(filtered.frame.camera_name)
        if camera:
            ring_frames = camera.get_ring_buffer()
            if ring_frames:
                context_frames_dir = _frame_store.save_ring_buffer(
                    frames=ring_frames,
                    camera_name=filtered.frame.camera_name,
                    trigger_timestamp=filtered.frame.timestamp,
                )

        # Send Telegram alert
        _alert_dispatcher.on_concern_event(
            analysis=result,
            camera=filtered.frame.camera_name,
            timestamp=filtered.frame.timestamp,
        )

    # 4. Store event in SQLite
    event_id = _db.insert_event(
        timestamp=filtered.frame.timestamp,
        camera=filtered.frame.camera_name,
        frame_type=ft,
        analysis=result,
        frame_path=frame_path,
        context_frames_dir=context_frames_dir,
        motion_score=filtered.motion_score,
    )

    with _stats_lock:
        _stats["events_stored"] += 1
        if result.get("lee_visible"):
            _stats["lee_visible"] += 1

    logger.debug(f"[{filtered.frame.camera_name}] Event #{event_id} stored")


# ---------------------------------------------------------------------------
# Burst sequence handler — analyze motion across frames
# ---------------------------------------------------------------------------

def handle_burst_sequence(frames: list, camera_name: str):
    """
    Called when burst mode ends with a collected sequence of frames.
    Sends them to Gemini as a sequence for motion-aware analysis.
    """
    if not frames:
        return

    logger.info(
        f"[{camera_name}] Burst sequence: analyzing {len(frames)} frames"
    )

    # Extract images from Frame objects
    images = [f.image for f in frames]

    # Send sequence to Gemini
    if config.BURST_SEQUENCE_ANALYSIS:
        result = _analyzer.analyze_sequence(images, camera_name=camera_name)
    else:
        # Fallback: just analyze the middle frame
        mid = len(images) // 2
        result = _analyzer.analyze_frame(images[mid], camera_name=camera_name)

    with _stats_lock:
        _stats["gemini_calls"] += 1

    if result is None:
        with _stats_lock:
            _stats["gemini_failures"] += 1
        logger.warning(f"[{camera_name}] Burst sequence analysis failed")
        return

    detail = result.get("activity_detail", "")
    movement = result.get("movement_quality", "")
    if detail:
        logger.info(f"[{camera_name}] → {detail}")
    if movement:
        logger.info(f"[{camera_name}] → Movement: {movement}")

    # Save the middle frame to disk as representative
    mid_frame = frames[len(frames) // 2]
    frame_path = _frame_store.save_frame(
        image=mid_frame.image,
        camera_name=camera_name,
        timestamp=mid_frame.timestamp,
        frame_type="burst",
    )

    # Check for concern → alert
    concern = result.get("concern_level", "none")
    context_frames_dir = None
    if concern in ("medium", "high"):
        with _stats_lock:
            _stats["concerns"] += 1

        # Save all burst frames as context
        camera = _capture_manager.get_camera(camera_name)
        if camera:
            ring_frames = camera.get_ring_buffer()
            if ring_frames:
                context_frames_dir = _frame_store.save_ring_buffer(
                    frames=ring_frames,
                    camera_name=camera_name,
                    trigger_timestamp=mid_frame.timestamp,
                )

        _alert_dispatcher.on_concern_event(
            analysis=result,
            camera=camera_name,
            timestamp=mid_frame.timestamp,
        )

    # Store event in SQLite
    _db.insert_event(
        timestamp=mid_frame.timestamp,
        camera=camera_name,
        frame_type="burst_sequence",
        analysis=result,
        frame_path=frame_path,
        context_frames_dir=context_frames_dir,
        motion_score=0.0,
    )

    with _stats_lock:
        _stats["events_stored"] += 1
        if result.get("lee_visible"):
            _stats["lee_visible"] += 1


# ---------------------------------------------------------------------------
# Periodic cleanup
# ---------------------------------------------------------------------------

def cleanup_loop(shutdown_event: threading.Event):
    """Periodically clean up old frames."""
    while not shutdown_event.is_set():
        shutdown_event.wait(timeout=3600)
        if not shutdown_event.is_set():
            try:
                _frame_store.cleanup_old_frames()
            except Exception as e:
                logger.error(f"Frame cleanup error: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _analyzer, _db, _frame_store, _capture_manager, _alert_dispatcher

    logger.info("=" * 60)
    logger.info("  Gabriel — Starting up")
    logger.info("=" * 60)

    # Validate API keys
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set. Add it to your .env file.")
        sys.exit(1)
    if not config.ANTHROPIC_API_KEY:
        logger.warning(
            "ANTHROPIC_API_KEY not set. Chat endpoint will not work. "
            "Add it to your .env file."
        )

    # Ensure storage directories exist
    config.BASE_DIR.mkdir(parents=True, exist_ok=True)
    config.FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize components
    _analyzer = VisionAnalyzer()
    _db = EventDB()
    _frame_store = FrameStore()
    _alert_dispatcher = AlertDispatcher(_db)
    logger.info("All components initialized")

    # Send a test alert on startup
    _alert_dispatcher.send_test_alert()

    # Start cameras
    _capture_manager = CaptureManager()
    _capture_manager.start_all()

    # Start filter pipeline for each camera
    for name, camera in _capture_manager.cameras.items():
        filt = FrameFilter(camera)
        filt.set_callback(handle_accepted_frame)
        filt.set_burst_callback(handle_burst_sequence)

        t = threading.Thread(
            target=filt.run,
            name=f"filter-{name}",
            daemon=True,
        )
        t.start()
        logger.info(f"Filter pipeline started for camera: {name}")

    # Start background threads
    shutdown_event = threading.Event()

    # Cleanup thread
    cleanup_thread = threading.Thread(
        target=cleanup_loop,
        args=(shutdown_event,),
        name="frame-cleanup",
        daemon=True,
    )
    cleanup_thread.start()

    # Inactivity monitor thread
    inactivity_thread = threading.Thread(
        target=_alert_dispatcher.start_inactivity_monitor,
        args=(shutdown_event,),
        name="inactivity-monitor",
        daemon=True,
    )
    inactivity_thread.start()

    # Audio monitor
    _audio_manager = AudioManager()

    def handle_sound_detected(camera_name, clip_path, duration, timestamp_iso):
        """Called when significant audio is detected."""
        logger.info(
            f"[{camera_name}] Sound detected: {duration:.1f}s clip saved to {clip_path}"
        )
        # Store audio event in database
        audio_analysis = {
            "lee_visible": True,
            "lee_location": camera_name,
            "activity": "vocalizing",
            "activity_detail": f"Sound detected from {camera_name} camera ({duration:.1f}s clip)",
            "posture": None,
            "energy_level": "medium",
            "concern_level": "none",
            "concern_detail": None,
            "environment_notes": f"Audio clip: {clip_path}",
        }
        _db.insert_event(
            timestamp=datetime.fromisoformat(timestamp_iso),
            camera=camera_name,
            frame_type="audio",
            analysis=audio_analysis,
            frame_path=clip_path,  # reuse frame_path for audio clip path
            motion_score=0.0,
        )

    _audio_manager.start_all(shutdown_event, callback=handle_sound_detected)

    # Graceful shutdown on Ctrl+C
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Gabriel is running. Press Ctrl+C to stop.")
    logger.info(f"Cameras: {list(_capture_manager.cameras.keys())}")
    logger.info(f"Motion threshold: {config.MOTION_THRESHOLD}%")
    logger.info(f"Dedup threshold: {config.DEDUP_THRESHOLD}")
    logger.info(f"Heartbeat interval: {config.HEARTBEAT_INTERVAL}s")
    logger.info(f"Burst threshold: {config.BURST_MOTION_THRESHOLD}%")
    logger.info(f"Database: {config.DB_PATH}")
    logger.info(f"Frames: {config.FRAMES_DIR}")
    logger.info(f"API server: http://{config.API_HOST}:{config.API_PORT}")

    # Create and start the API server
    app = create_app(
        db=_db,
        capture_manager=_capture_manager,
        analyzer=_analyzer,
        frame_store=_frame_store,
    )

    try:
        uvicorn.run(
            app,
            host=config.API_HOST,
            port=config.API_PORT,
            log_level="warning",
        )
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down...")
        shutdown_event.set()
        _capture_manager.stop_all()
        _db.close()

        logger.info("Final stats:")
        with _stats_lock:
            for key, val in _stats.items():
                logger.info(f"  {key}: {val}")

        logger.info("Gabriel stopped. Goodbye!")


if __name__ == "__main__":
    main()
