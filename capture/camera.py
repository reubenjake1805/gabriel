"""
Gabriel — Camera Capture Module

Grabs frames from the video source (webcam or RTSP) at a configurable
interval and maintains a rolling ring buffer of recent frames for
concern-event context.
"""

import cv2
import time
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """A single captured frame with metadata."""
    image: any                  # numpy array (BGR)
    timestamp: datetime
    camera_name: str
    capture_index: int = 0      # sequential counter


class CameraStream:
    """
    Captures frames from a single camera source.

    Runs in its own thread. Maintains a ring buffer of the last N seconds
    of frames. Provides the latest frame on demand and can flush the
    buffer to disk when a concern event is triggered.
    """

    def __init__(self, name: str, source):
        """
        Args:
            name:   Camera identifier (e.g. "webcam", "living_room").
            source: OpenCV source — int for device index, str for RTSP URL.
        """
        self.name = name
        self.source = source
        self._cap = None
        self._running = False
        self._thread = None
        self._capture_index = 0

        # Ring buffer: stores the last RING_BUFFER_SECONDS of frames
        self._ring_buffer = deque(maxlen=config.RING_BUFFER_SECONDS)
        self._ring_lock = threading.Lock()

        # Latest frame (for quick access by other modules)
        self._latest_frame = None
        self._latest_lock = threading.Lock()

        # Capture interval (can be temporarily changed by burst mode)
        self._capture_interval = config.CAPTURE_INTERVAL
        self._interval_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Open the camera and start the capture thread."""
        logger.info(f"[{self.name}] Opening camera source: {self.source}")
        self._cap = cv2.VideoCapture(self.source)

        if not self._cap.isOpened():
            raise RuntimeError(
                f"[{self.name}] Failed to open camera source: {self.source}"
            )

        # Log camera properties
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"[{self.name}] Opened: {w}x{h} @ {fps} fps")

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera-{self.name}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[{self.name}] Capture thread started")

    def stop(self):
        """Stop the capture thread and release the camera."""
        logger.info(f"[{self.name}] Stopping capture")
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._cap:
            self._cap.release()
        logger.info(f"[{self.name}] Stopped")

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _capture_loop(self):
        """Main loop: grab frames at the configured interval."""
        while self._running:
            loop_start = time.monotonic()

            ret, image = self._cap.read()

            if not ret:
                logger.warning(f"[{self.name}] Frame grab failed, retrying...")
                self._reconnect()
                time.sleep(1.0)
                continue

            now = datetime.now(timezone.utc)
            self._capture_index += 1

            frame = Frame(
                image=image,
                timestamp=now,
                camera_name=self.name,
                capture_index=self._capture_index,
            )

            # Update ring buffer
            with self._ring_lock:
                self._ring_buffer.append(frame)

            # Update latest frame
            with self._latest_lock:
                self._latest_frame = frame

            # Sleep for the remainder of the capture interval
            with self._interval_lock:
                interval = self._capture_interval
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _reconnect(self):
        """Attempt to reconnect to the camera source (for RTSP drops)."""
        logger.info(f"[{self.name}] Attempting reconnect...")
        if self._cap:
            self._cap.release()

        for attempt in range(1, 6):
            self._cap = cv2.VideoCapture(self.source)
            if self._cap.isOpened():
                logger.info(f"[{self.name}] Reconnected on attempt {attempt}")
                return
            logger.warning(f"[{self.name}] Reconnect attempt {attempt} failed")
            time.sleep(2.0 * attempt)  # exponential-ish backoff

        logger.error(f"[{self.name}] Failed to reconnect after 5 attempts")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_latest_frame(self) -> Frame | None:
        """Return the most recently captured frame, or None."""
        with self._latest_lock:
            return self._latest_frame

    def get_ring_buffer(self) -> list[Frame]:
        """Return a snapshot of the ring buffer (copy of the deque)."""
        with self._ring_lock:
            return list(self._ring_buffer)

    def set_capture_interval(self, interval: float):
        """
        Temporarily change the capture interval (used by burst mode).
        Call with config.CAPTURE_INTERVAL to reset to normal.
        """
        with self._interval_lock:
            old = self._capture_interval
            self._capture_interval = interval
            if old != interval:
                logger.info(
                    f"[{self.name}] Capture interval: {old}s → {interval}s"
                )

    @property
    def is_running(self) -> bool:
        return self._running


class CaptureManager:
    """
    Manages all camera streams defined in config.CAMERA_SOURCES.
    Provides a unified interface to start/stop cameras and access frames.
    """

    def __init__(self):
        self.cameras: dict[str, CameraStream] = {}

    def start_all(self):
        """Initialize and start all configured cameras."""
        for name, source in config.CAMERA_SOURCES.items():
            stream = CameraStream(name, source)
            stream.start()
            self.cameras[name] = stream
        logger.info(f"Started {len(self.cameras)} camera(s)")

    def stop_all(self):
        """Stop all camera streams."""
        for stream in self.cameras.values():
            stream.stop()
        logger.info("All cameras stopped")

    def get_latest_frame(self, camera_name: str = None) -> Frame | None:
        """
        Get the latest frame from a specific camera, or from the first
        available camera if no name is given.
        """
        if camera_name:
            stream = self.cameras.get(camera_name)
            return stream.get_latest_frame() if stream else None

        # Default: return from the first camera that has a frame
        for stream in self.cameras.values():
            frame = stream.get_latest_frame()
            if frame is not None:
                return frame
        return None

    def get_ring_buffer(self, camera_name: str) -> list[Frame]:
        """Get the ring buffer snapshot for a specific camera."""
        stream = self.cameras.get(camera_name)
        return stream.get_ring_buffer() if stream else []

    def get_camera(self, camera_name: str) -> CameraStream | None:
        """Get a specific camera stream by name."""
        return self.cameras.get(camera_name)
