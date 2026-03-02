"""
Gabriel — Frame Filter Pipeline

Three-tier filtering to reduce raw frames (~36,000/day) down to
~100–170 frames that actually get sent to the vision API.

Tier 1: Motion detection (OpenCV absdiff) — discard static scenes
Tier 2: Perceptual deduplication (pHash) — discard visually similar frames
Tier 3: Heartbeat — force-send one frame every N minutes regardless

Plus burst mode: when extreme motion is detected, temporarily increase
capture rate and bypass dedup for better coverage of sudden events.
"""

import cv2
import time
import logging
import threading
import numpy as np
from dataclasses import dataclass
from enum import Enum, auto

import imagehash
from PIL import Image

import config
from capture.camera import Frame, CameraStream

logger = logging.getLogger(__name__)


class FrameType(Enum):
    """Why this frame was selected for analysis."""
    MOTION = auto()
    HEARTBEAT = auto()
    BURST = auto()


@dataclass
class FilteredFrame:
    """A frame that passed all filters and should be sent to the vision API."""
    frame: Frame
    frame_type: FrameType
    motion_score: float         # percentage of pixels that changed


class BurstState:
    """Tracks the state of burst mode for a single camera."""

    def __init__(self):
        self.active = False
        self.started_at = 0.0
        self.frames_captured = 0
        self.frames_analyzed = 0
        self._lock = threading.Lock()

    def activate(self):
        with self._lock:
            self.active = True
            self.started_at = time.monotonic()
            self.frames_captured = 0
            self.frames_analyzed = 0
            logger.warning("BURST MODE activated")

    def deactivate(self):
        with self._lock:
            self.active = False
            logger.info(
                f"BURST MODE ended — captured {self.frames_captured} frames, "
                f"analyzed {self.frames_analyzed}"
            )

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.active

    @property
    def is_expired(self) -> bool:
        with self._lock:
            if not self.active:
                return False
            elapsed = time.monotonic() - self.started_at
            return elapsed >= config.BURST_DURATION_SECONDS

    def should_analyze(self) -> bool:
        """
        Decide whether this burst frame should be sent to Gemini.
        We spread the BURST_ANALYZE_COUNT evenly across the burst window.
        """
        with self._lock:
            if not self.active:
                return False
            self.frames_captured += 1
            total_expected = config.BURST_FPS * config.BURST_DURATION_SECONDS
            analyze_every = max(1, total_expected // config.BURST_ANALYZE_COUNT)
            if self.frames_captured % analyze_every == 0:
                self.frames_analyzed += 1
                return True
            return False


class FrameFilter:
    """
    Processes raw frames from a camera and decides which ones
    should be sent to the vision API for analysis.
    """

    def __init__(self, camera: CameraStream):
        self.camera = camera
        self._previous_gray = None
        self._last_analyzed_hash = None
        self._last_analysis_time = 0.0
        self._burst = BurstState()

        # Callback: called with a FilteredFrame whenever a frame passes
        self._on_frame_accepted = None

    def set_callback(self, callback):
        """
        Register a callback that's invoked whenever a frame passes
        the filter pipeline.

        Args:
            callback: Callable[[FilteredFrame], None]
        """
        self._on_frame_accepted = callback

    # ------------------------------------------------------------------
    # Main processing loop
    # ------------------------------------------------------------------

    def run(self):
        """
        Main loop: continuously pull the latest frame from the camera
        and run it through the filter pipeline.

        This runs in its own thread, polling the camera at the capture
        interval. It doesn't grab frames directly — the CameraStream
        does that. This module just reads the latest frame and decides
        whether it's worth analyzing.
        """
        logger.info(f"[{self.camera.name}] Filter pipeline started")
        last_processed_index = -1

        while self.camera.is_running:
            frame = self.camera.get_latest_frame()

            if frame is None or frame.capture_index == last_processed_index:
                time.sleep(0.05)  # avoid busy-waiting
                continue

            last_processed_index = frame.capture_index

            # Check if burst mode has expired
            if self._burst.is_active and self._burst.is_expired:
                self._end_burst_mode()

            # Run the filter pipeline
            result = self._evaluate(frame)

            if result is not None and self._on_frame_accepted:
                self._on_frame_accepted(result)

    # ------------------------------------------------------------------
    # Filter logic
    # ------------------------------------------------------------------

    def _evaluate(self, frame: Frame) -> FilteredFrame | None:
        """
        Run a frame through the three-tier filter pipeline.
        Returns a FilteredFrame if the frame should be analyzed, None otherwise.
        """
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (config.MOTION_BLUR_KERNEL,) * 2, 0)

        # ------ BURST MODE (bypass normal filtering) ------
        if self._burst.is_active:
            if self._burst.should_analyze():
                self._update_state(gray, frame)
                return FilteredFrame(
                    frame=frame,
                    frame_type=FrameType.BURST,
                    motion_score=self._compute_motion(gray),
                )
            return None  # burst frame saved to disk but not analyzed

        # ------ TIER 1: Motion Detection ------
        motion_score = self._compute_motion(gray)

        # Check for extreme motion → trigger burst mode
        if motion_score >= config.BURST_MOTION_THRESHOLD:
            self._start_burst_mode()
            self._update_state(gray, frame)
            return FilteredFrame(
                frame=frame,
                frame_type=FrameType.BURST,
                motion_score=motion_score,
            )

        if motion_score >= config.MOTION_THRESHOLD:
            # ------ TIER 2: Perceptual Deduplication ------
            if not self._is_duplicate(frame.image):
                self._update_state(gray, frame)
                return FilteredFrame(
                    frame=frame,
                    frame_type=FrameType.MOTION,
                    motion_score=motion_score,
                )

        # ------ TIER 3: Heartbeat ------
        now = time.monotonic()
        if now - self._last_analysis_time >= config.HEARTBEAT_INTERVAL:
            self._update_state(gray, frame)
            return FilteredFrame(
                frame=frame,
                frame_type=FrameType.HEARTBEAT,
                motion_score=motion_score,
            )

        # Update previous frame for next comparison even if we discard
        self._previous_gray = gray
        return None

    def _compute_motion(self, gray: np.ndarray) -> float:
        """
        Compute the motion score: percentage of pixels that changed
        significantly between this frame and the previous one.
        """
        if self._previous_gray is None:
            self._previous_gray = gray
            return 0.0

        diff = cv2.absdiff(gray, self._previous_gray)
        _, thresh = cv2.threshold(
            diff, config.MOTION_BINARY_THRESH, 255, cv2.THRESH_BINARY
        )
        changed_pixels = cv2.countNonZero(thresh)
        total_pixels = thresh.size
        return (changed_pixels / total_pixels) * 100

    def _is_duplicate(self, image: np.ndarray) -> bool:
        """
        Check if the frame is perceptually similar to the last analyzed frame
        using pHash (perceptual hashing).
        """
        if self._last_analyzed_hash is None:
            return False  # first frame is never a duplicate

        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        current_hash = imagehash.phash(pil_image)
        distance = current_hash - self._last_analyzed_hash

        is_dup = distance < config.DEDUP_THRESHOLD
        if is_dup:
            logger.debug(
                f"[{self.camera.name}] Dedup: hash distance {distance} "
                f"< threshold {config.DEDUP_THRESHOLD}, skipping"
            )
        return is_dup

    def _update_state(self, gray: np.ndarray, frame: Frame):
        """Update tracking state after a frame is accepted for analysis."""
        self._previous_gray = gray
        self._last_analysis_time = time.monotonic()

        # Update perceptual hash
        pil_image = Image.fromarray(
            cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)
        )
        self._last_analyzed_hash = imagehash.phash(pil_image)

    # ------------------------------------------------------------------
    # Burst mode
    # ------------------------------------------------------------------

    def _start_burst_mode(self):
        """Activate burst mode: increase capture rate, bypass dedup."""
        self._burst.activate()
        # Tell the camera to capture faster
        burst_interval = 1.0 / config.BURST_FPS
        self.camera.set_capture_interval(burst_interval)

    def _end_burst_mode(self):
        """Deactivate burst mode: restore normal capture rate."""
        self._burst.deactivate()
        self.camera.set_capture_interval(config.CAPTURE_INTERVAL)
