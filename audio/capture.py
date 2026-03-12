"""
Gabriel — Audio Capture

Monitors RTSP audio streams from cameras for significant sounds.
When sound is detected above the noise threshold, saves an audio clip
and logs it as an event.
"""

import logging
import subprocess
import threading
import time
import wave
import struct
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUDIO_SAMPLE_RATE = 8000       # matches camera's PCM A-law output
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SECONDS = 1        # analyze audio in 1-second chunks
NOISE_THRESHOLD_DB = -30       # dB threshold — sounds above this are "significant"
MIN_SOUND_DURATION = 1.5       # minimum seconds of sound to save a clip
PRE_SOUND_BUFFER = 2           # seconds of audio to keep before the sound starts
POST_SOUND_SECONDS = 2         # seconds to keep recording after sound drops
MAX_CLIP_SECONDS = 30          # maximum clip length
CLIP_FORMAT = "wav"

# Frequency filtering — cat vocalizations are typically 500-2000 Hz
# Doors, drawers, footsteps are mostly below 300 Hz
HIGH_FREQ_MIN_HZ = 400        # minimum frequency to consider "high-pitched"
HIGH_FREQ_RATIO = 0.3         # at least 30% of energy must be above HIGH_FREQ_MIN_HZ


class AudioMonitor:
    """
    Monitors a camera's RTSP audio stream for significant sounds.
    Runs ffmpeg in a subprocess to extract raw PCM audio, then
    analyzes volume levels to detect sounds.
    """

    def __init__(self, camera_name: str, rtsp_url: str, audio_dir: Path = None):
        self._camera_name = camera_name
        self._rtsp_url = rtsp_url
        self._audio_dir = audio_dir or config.BASE_DIR / "audio"
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        self._process = None
        self._running = False
        self._on_sound_detected = None

    def set_callback(self, callback):
        """
        Register a callback for when a sound clip is saved.

        Args:
            callback: Callable[[str, str, float, str], None]
                      (camera_name, clip_path, duration_seconds, timestamp_iso)
        """
        self._on_sound_detected = callback

    def start(self, shutdown_event: threading.Event):
        """
        Start monitoring audio in a loop. Runs in its own thread.
        Restarts ffmpeg if it dies.
        """
        self._running = True
        logger.info(f"[{self._camera_name}] Audio monitor starting")

        while self._running and not shutdown_event.is_set():
            try:
                self._run_monitor(shutdown_event)
            except Exception as e:
                logger.error(f"[{self._camera_name}] Audio monitor error: {e}")
                if not shutdown_event.is_set():
                    logger.info(f"[{self._camera_name}] Restarting audio monitor in 5s")
                    shutdown_event.wait(timeout=5)

        logger.info(f"[{self._camera_name}] Audio monitor stopped")

    def stop(self):
        """Stop the audio monitor."""
        self._running = False
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass

    def _run_monitor(self, shutdown_event: threading.Event):
        """
        Run ffmpeg to extract raw PCM audio from the RTSP stream,
        then analyze it for significant sounds.
        """
        # Start ffmpeg to extract raw PCM audio
        cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", self._rtsp_url,
            "-vn",                      # no video
            "-acodec", "pcm_s16le",     # convert to 16-bit PCM
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", str(AUDIO_CHANNELS),
            "-f", "s16le",              # raw PCM output
            "-loglevel", "error",
            "pipe:1",                   # output to stdout
        ]

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        logger.info(f"[{self._camera_name}] ffmpeg audio stream started (pid={self._process.pid})")

        bytes_per_sample = 2  # 16-bit
        chunk_size = AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * bytes_per_sample * AUDIO_CHUNK_SECONDS

        # Rolling buffer for pre-sound context
        pre_buffer_chunks = PRE_SOUND_BUFFER  # seconds
        pre_buffer = []

        # Sound detection state
        recording = False
        sound_chunks = []
        silence_countdown = 0
        sound_start_time = None

        while self._running and not shutdown_event.is_set():
            raw_data = self._process.stdout.read(chunk_size)
            if not raw_data or len(raw_data) < chunk_size:
                # ffmpeg died or stream ended
                logger.warning(f"[{self._camera_name}] Audio stream ended")
                break

            # Calculate RMS volume in dB
            db_level = self._calculate_db(raw_data)

            is_loud = db_level > NOISE_THRESHOLD_DB
            is_high_pitched = self._is_high_pitched(raw_data) if is_loud else False
            is_sound = is_loud and is_high_pitched

            if not recording:
                # Keep a rolling pre-buffer
                pre_buffer.append(raw_data)
                if len(pre_buffer) > pre_buffer_chunks:
                    pre_buffer.pop(0)

                if is_sound:
                    # Sound detected — start recording
                    recording = True
                    sound_chunks = list(pre_buffer)  # include pre-buffer
                    sound_chunks.append(raw_data)
                    silence_countdown = POST_SOUND_SECONDS
                    sound_start_time = datetime.now(timezone.utc)
                    logger.info(
                        f"[{self._camera_name}] Sound detected "
                        f"({db_level:.1f} dB, high-freq)"
                    )
                elif is_loud:
                    logger.debug(
                        f"[{self._camera_name}] Low-freq sound ignored "
                        f"({db_level:.1f} dB)"
                    )
            else:
                # Currently recording
                sound_chunks.append(raw_data)

                if is_sound:
                    silence_countdown = POST_SOUND_SECONDS
                else:
                    silence_countdown -= AUDIO_CHUNK_SECONDS

                # Check if we should stop recording
                total_seconds = len(sound_chunks) * AUDIO_CHUNK_SECONDS
                if silence_countdown <= 0 or total_seconds >= MAX_CLIP_SECONDS:
                    # Save the clip
                    if total_seconds >= MIN_SOUND_DURATION:
                        clip_path = self._save_clip(sound_chunks, sound_start_time)
                        if clip_path and self._on_sound_detected:
                            self._on_sound_detected(
                                self._camera_name,
                                clip_path,
                                total_seconds,
                                sound_start_time.isoformat(),
                            )
                    else:
                        logger.debug(
                            f"[{self._camera_name}] Sound too short "
                            f"({total_seconds:.1f}s), discarding"
                        )

                    # Reset
                    recording = False
                    sound_chunks = []
                    pre_buffer = []
                    sound_start_time = None

        # Cleanup
        if self._process:
            self._process.kill()
            self._process = None

    def _calculate_db(self, raw_data: bytes) -> float:
        """Calculate RMS volume in dB from raw PCM 16-bit data."""
        # Unpack 16-bit signed integers
        num_samples = len(raw_data) // 2
        if num_samples == 0:
            return -100.0

        samples = struct.unpack(f"<{num_samples}h", raw_data)

        # Calculate RMS
        sum_squares = sum(s * s for s in samples)
        rms = math.sqrt(sum_squares / num_samples)

        if rms == 0:
            return -100.0

        # Convert to dB (relative to max 16-bit value)
        db = 20 * math.log10(rms / 32768.0)
        return db

    def _is_high_pitched(self, raw_data: bytes) -> bool:
        """
        Check if the sound has significant energy above HIGH_FREQ_MIN_HZ.
        Uses FFT to analyze frequency content.
        Cat meows are typically 500-2000 Hz.
        Door slams, footsteps, drawers are mostly below 300 Hz.
        """
        import numpy as np

        num_samples = len(raw_data) // 2
        if num_samples == 0:
            return False

        samples = np.array(
            struct.unpack(f"<{num_samples}h", raw_data),
            dtype=np.float64,
        )

        # Apply FFT
        fft_result = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(num_samples, d=1.0 / AUDIO_SAMPLE_RATE)

        # Calculate energy in high-frequency band vs total
        total_energy = np.sum(fft_result ** 2)
        if total_energy == 0:
            return False

        high_freq_mask = freqs >= HIGH_FREQ_MIN_HZ
        high_energy = np.sum(fft_result[high_freq_mask] ** 2)

        ratio = high_energy / total_energy
        return ratio >= HIGH_FREQ_RATIO

    def _save_clip(self, chunks: list, timestamp: datetime) -> str | None:
        """Save audio chunks as a WAV file."""
        try:
            date_dir = self._audio_dir / timestamp.strftime("%Y-%m-%d")
            date_dir.mkdir(parents=True, exist_ok=True)

            time_str = timestamp.strftime("%H%M%S")
            filename = f"{self._camera_name}_{time_str}.wav"
            filepath = date_dir / filename

            # Avoid overwriting
            counter = 1
            while filepath.exists():
                filename = f"{self._camera_name}_{time_str}_{counter}.wav"
                filepath = date_dir / filename
                counter += 1

            # Write WAV file
            raw_data = b"".join(chunks)
            with wave.open(str(filepath), "wb") as wf:
                wf.setnchannels(AUDIO_CHANNELS)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(AUDIO_SAMPLE_RATE)
                wf.writeframes(raw_data)

            duration = len(raw_data) / (AUDIO_SAMPLE_RATE * 2 * AUDIO_CHANNELS)
            logger.info(
                f"[{self._camera_name}] Audio clip saved: {filepath} "
                f"({duration:.1f}s)"
            )
            return str(filepath)

        except Exception as e:
            logger.error(f"[{self._camera_name}] Failed to save audio clip: {e}")
            return None


class AudioManager:
    """Manages audio monitors for all cameras."""

    def __init__(self):
        self._monitors = {}
        self._threads = []

    def start_all(self, shutdown_event: threading.Event, callback=None):
        """Start audio monitoring for all cameras in config."""
        for camera_name, source in config.CAMERA_SOURCES.items():
            if not isinstance(source, str) or not source.startswith("rtsp://"):
                logger.info(f"[{camera_name}] Skipping audio — not an RTSP source")
                continue

            # Use TCP transport in the URL for audio access
            monitor = AudioMonitor(camera_name, source)
            if callback:
                monitor.set_callback(callback)

            self._monitors[camera_name] = monitor

            t = threading.Thread(
                target=monitor.start,
                args=(shutdown_event,),
                name=f"audio-{camera_name}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
            logger.info(f"[{camera_name}] Audio monitor started")

    def stop_all(self):
        """Stop all audio monitors."""
        for monitor in self._monitors.values():
            monitor.stop()
