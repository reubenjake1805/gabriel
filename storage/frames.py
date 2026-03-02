"""
Gabriel — Frame File Management

Saves analyzed frames to disk and handles cleanup of old frames.
"""

import cv2
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class FrameStore:
    """
    Manages frame JPEG files on disk.

    Directory structure:
        ~/gabriel/frames/
          2026-03-01/
            webcam_143200_motion.jpg
            webcam_144500_heartbeat.jpg
            concern_143201/
              context_143130.jpg
              ...
    """

    def __init__(self, frames_dir: Path = None):
        self._frames_dir = frames_dir or config.FRAMES_DIR
        self._frames_dir.mkdir(parents=True, exist_ok=True)

    def save_frame(
        self,
        image,
        camera_name: str,
        timestamp: datetime,
        frame_type: str,
    ) -> str:
        """
        Save a frame to disk.

        Args:
            image: numpy array (BGR from OpenCV)
            camera_name: e.g. "webcam"
            timestamp: when the frame was captured
            frame_type: "motion", "heartbeat", or "burst"

        Returns:
            The file path where the frame was saved.
        """
        date_dir = self._frames_dir / timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        time_str = timestamp.strftime("%H%M%S")
        filename = f"{camera_name}_{time_str}_{frame_type}.jpg"
        filepath = date_dir / filename

        # Avoid overwriting if same second
        counter = 1
        while filepath.exists():
            filename = f"{camera_name}_{time_str}_{frame_type}_{counter}.jpg"
            filepath = date_dir / filename
            counter += 1

        cv2.imwrite(
            str(filepath),
            image,
            [cv2.IMWRITE_JPEG_QUALITY, config.FRAME_JPEG_QUALITY],
        )

        logger.debug(f"Frame saved: {filepath}")
        return str(filepath)

    def save_ring_buffer(
        self,
        frames: list,
        camera_name: str,
        trigger_timestamp: datetime,
    ) -> str:
        """
        Flush the ring buffer to disk for a concern event.

        Args:
            frames: list of Frame objects from the ring buffer
            camera_name: e.g. "webcam"
            trigger_timestamp: when the concern event happened

        Returns:
            Path to the concern directory containing all context frames.
        """
        date_dir = self._frames_dir / trigger_timestamp.strftime("%Y-%m-%d")
        time_str = trigger_timestamp.strftime("%H%M%S")
        concern_dir = date_dir / f"concern_{time_str}"
        concern_dir.mkdir(parents=True, exist_ok=True)

        for frame in frames:
            frame_time = frame.timestamp.strftime("%H%M%S_%f")[:-3]  # ms precision
            filename = f"context_{frame_time}.jpg"
            filepath = concern_dir / filename

            cv2.imwrite(
                str(filepath),
                frame.image,
                [cv2.IMWRITE_JPEG_QUALITY, config.FRAME_JPEG_QUALITY],
            )

        logger.info(
            f"Ring buffer saved: {len(frames)} frames → {concern_dir}"
        )
        return str(concern_dir)

    def cleanup_old_frames(self):
        """
        Delete frame directories older than FRAME_RETENTION_HOURS.
        Called periodically (e.g. once per hour).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=config.FRAME_RETENTION_HOURS
        )
        cutoff_date_str = cutoff.strftime("%Y-%m-%d")

        deleted_count = 0
        for date_dir in sorted(self._frames_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            # Compare directory name (date string) to cutoff
            if date_dir.name < cutoff_date_str:
                shutil.rmtree(date_dir)
                deleted_count += 1
                logger.info(f"Cleaned up old frames: {date_dir}")

        if deleted_count:
            logger.info(f"Frame cleanup: removed {deleted_count} directories")

    def get_frame_path(self, relative_path: str) -> Path | None:
        """
        Get the full path to a frame file, if it exists.
        Used by the API to serve frames.
        """
        full_path = Path(relative_path)
        if full_path.exists():
            return full_path
        return None
