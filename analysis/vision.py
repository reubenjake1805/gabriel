"""
Gabriel — Gemini Vision Analyzer

Sends frames to Gemini 2.5 Flash and receives structured JSON
descriptions of what Lee is doing.

Uses the new google-genai SDK (not the deprecated google-generativeai).
"""

import io
import json
import logging
import time

import cv2
from PIL import Image
from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for Gemini
# ---------------------------------------------------------------------------

VISION_SYSTEM_PROMPT = """You are an AI assistant analyzing security camera footage of a home where
a 10-month-old cat named Rock Lee (called "Lee") lives.

Analyze this camera frame and respond with ONLY a JSON object — no markdown,
no code fences, no explanation. Just the raw JSON:

{
  "lee_visible": boolean,
  "lee_location": string or null,
  "activity": string,
  "activity_detail": string,
  "posture": string or null,
  "energy_level": string,
  "concern_level": "none" | "low" | "medium" | "high",
  "concern_detail": string or null,
  "environment_notes": string or null
}

Field guidelines:
- lee_visible: true if you can see Lee in the frame, false otherwise.
- lee_location: where Lee is (e.g. "on the couch", "near the feeder", "by the window"). null if not visible.
- activity: one of: "eating", "drinking", "sleeping", "playing", "grooming", "using_litter_box", "exploring", "resting", "looking_outside", "running", "climbing", "hiding", "not_visible", "other"
- activity_detail: free-text description of what Lee is doing (1-2 sentences).
- posture: e.g. "curled up", "stretched out", "sitting upright", "crouching", "standing", "lying on side". null if not visible.
- energy_level: "low", "medium", or "high"
- concern_level: "none" for normal behavior. "low" for slightly unusual. "medium" for potentially concerning (limping, lethargy). "high" for urgent (injury, fall, distress, vomiting).
- concern_detail: explain the concern. null if concern_level is "none".
- environment_notes: anything notable about the room — lights, objects knocked over, doors open/closed, other animals. null if nothing notable.

If Lee is not visible in the frame, set lee_visible to false, activity to "not_visible",
and describe what you can see in the environment_notes field."""


SEQUENCE_SYSTEM_PROMPT = """You are an AI assistant analyzing a SEQUENCE of security camera frames
captured over a few seconds. A 10-month-old cat named Rock Lee (called "Lee") lives in this home.

These frames are in chronological order, captured about 0.4 seconds apart. Analyze Lee's MOVEMENT
across the sequence — not just one frame.

Respond with ONLY a JSON object — no markdown, no code fences, no explanation:

{
  "lee_visible": boolean,
  "lee_location": string or null,
  "activity": string,
  "activity_detail": string,
  "posture": string or null,
  "energy_level": string,
  "movement_quality": string or null,
  "concern_level": "none" | "low" | "medium" | "high",
  "concern_detail": string or null,
  "environment_notes": string or null
}

Field guidelines (same as single-frame, plus):
- lee_visible: true if Lee is visible in ANY of the frames.
- activity: one of: "eating", "drinking", "sleeping", "playing", "grooming", "using_litter_box", "exploring", "resting", "looking_outside", "running", "climbing", "hiding", "not_visible", "other"
- activity_detail: describe what Lee is doing ACROSS the sequence (1-3 sentences). Note changes between frames.
- movement_quality: describe how Lee is moving. Look for: normal gait, limping, favoring a leg, stumbling, unsteady, stiff, slow, fast, erratic, smooth. null if not moving or not visible.
- concern_level: Pay special attention to movement abnormalities across frames:
  - "none": normal movement, normal behavior
  - "low": slightly unusual movement or behavior
  - "medium": limping, unsteady gait, favoring a limb, unusual lethargy, repeated failed jumps
  - "high": falling, seizure-like movement, inability to stand, dragging a limb, signs of acute distress
- concern_detail: explain what you observed across the frames that concerns you. null if none.
- energy_level: judge from the speed and nature of movement across frames.

IMPORTANT: Compare frames to detect movement patterns. A single frame might look fine,
but the sequence might reveal limping, stumbling, or other movement issues."""


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class VisionAnalyzer:
    """Sends frames to Gemini and parses structured JSON responses."""

    def __init__(self):
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        self._model = config.GEMINI_MODEL
        logger.info(f"Vision analyzer initialized with model: {self._model}")

    def analyze_frame(self, image, camera_name: str = "unknown") -> dict | None:
        """
        Send a single frame to Gemini for analysis.

        Args:
            image: numpy array (BGR format from OpenCV)
            camera_name: which camera this frame is from (for logging)

        Returns:
            Parsed JSON dict with Lee's activity, or None if analysis failed.
        """
        start_time = time.monotonic()

        try:
            image_part = self._image_to_part(image)

            response = self._client.models.generate_content(
                model=self._model,
                contents=[VISION_SYSTEM_PROMPT, image_part],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0,
                    ),
                ),
            )

            elapsed = time.monotonic() - start_time
            raw_text = response.text.strip()

            result = self._parse_response(raw_text)

            if result:
                self._log_result(camera_name, result, elapsed)
                result["_raw_response"] = raw_text
                result["_analysis_time"] = round(elapsed, 2)
                return result
            else:
                logger.warning(
                    f"[{camera_name}] Failed to parse Gemini response "
                    f"({elapsed:.1f}s): {raw_text[:200]}"
                )
                return None

        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error(
                f"[{camera_name}] Gemini API error ({elapsed:.1f}s): {e}"
            )
            return None

    def analyze_sequence(self, images: list, camera_name: str = "unknown") -> dict | None:
        """
        Send a sequence of frames to Gemini for motion-aware analysis.
        Used during burst mode to detect movement abnormalities like limping.

        Args:
            images: list of numpy arrays (BGR format from OpenCV), chronological order
            camera_name: which camera these frames are from

        Returns:
            Parsed JSON dict with Lee's activity and movement analysis,
            or None if analysis failed.
        """
        if not images:
            return None

        start_time = time.monotonic()

        try:
            # Build content: prompt + all images
            contents = [SEQUENCE_SYSTEM_PROMPT]
            for i, image in enumerate(images):
                image_part = self._image_to_part(image)
                contents.append(f"Frame {i + 1} of {len(images)}:")
                contents.append(image_part)

            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0,
                    ),
                ),
            )

            elapsed = time.monotonic() - start_time
            raw_text = response.text.strip()

            result = self._parse_response(raw_text)

            if result:
                movement = result.get("movement_quality", "")
                self._log_result(camera_name, result, elapsed, is_sequence=True)
                if movement:
                    logger.info(f"[{camera_name}] Movement: {movement}")

                result["_raw_response"] = raw_text
                result["_analysis_time"] = round(elapsed, 2)
                result["_sequence_length"] = len(images)
                return result
            else:
                logger.warning(
                    f"[{camera_name}] Failed to parse sequence response "
                    f"({elapsed:.1f}s): {raw_text[:200]}"
                )
                return None

        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error(
                f"[{camera_name}] Gemini sequence API error ({elapsed:.1f}s): {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _image_to_part(self, image):
        """Convert an OpenCV BGR image to a Gemini image Part."""
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_image)

        buffer = io.BytesIO()
        pil_image.save(buffer, format="JPEG", quality=config.FRAME_JPEG_QUALITY)
        buffer.seek(0)
        jpeg_bytes = buffer.read()

        return types.Part.from_bytes(
            data=jpeg_bytes,
            mime_type="image/jpeg",
        )

    def _log_result(self, camera_name: str, result: dict, elapsed: float, is_sequence: bool = False):
        """Log the analysis result."""
        activity = result.get("activity", "unknown")
        concern = result.get("concern_level", "none")
        visible = result.get("lee_visible", False)
        mode = "Sequence analyzed" if is_sequence else "Analyzed"

        log_msg = (
            f"[{camera_name}] {mode} in {elapsed:.1f}s — "
            f"visible={visible}  activity={activity}  concern={concern}"
        )
        if concern in ("medium", "high"):
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

    def _parse_response(self, raw_text: str) -> dict | None:
        """
        Parse Gemini's response into a dict. Handles common quirks
        like markdown code fences around the JSON.
        """
        text = raw_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]).strip()

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Retry: try to find JSON object in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    result = json.loads(text[start:end])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        # Validate required fields
        required = ["lee_visible", "activity", "concern_level"]
        for field in required:
            if field not in result:
                logger.warning(f"Missing required field: {field}")
                return None

        # Normalize concern_level
        valid_concerns = {"none", "low", "medium", "high"}
        if result["concern_level"] not in valid_concerns:
            result["concern_level"] = "none"

        return result
