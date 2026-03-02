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
        Send a frame to Gemini for analysis.

        Args:
            image: numpy array (BGR format from OpenCV)
            camera_name: which camera this frame is from (for logging)

        Returns:
            Parsed JSON dict with Lee's activity, or None if analysis failed.
        """
        start_time = time.monotonic()

        try:
            # Convert OpenCV BGR numpy array to PIL Image
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_image)

            # Compress to JPEG in memory
            buffer = io.BytesIO()
            pil_image.save(buffer, format="JPEG", quality=config.FRAME_JPEG_QUALITY)
            buffer.seek(0)
            jpeg_bytes = buffer.read()

            # Build the image part
            image_part = types.Part.from_bytes(
                data=jpeg_bytes,
                mime_type="image/jpeg",
            )

            # Send to Gemini
            response = self._client.models.generate_content(
                model=self._model,
                contents=[VISION_SYSTEM_PROMPT, image_part],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0,  # disable thinking for speed
                    ),
                ),
            )

            elapsed = time.monotonic() - start_time
            raw_text = response.text.strip()

            # Parse JSON response
            result = self._parse_response(raw_text)

            if result:
                activity = result.get("activity", "unknown")
                concern = result.get("concern_level", "none")
                visible = result.get("lee_visible", False)

                log_msg = (
                    f"[{camera_name}] Analyzed in {elapsed:.1f}s — "
                    f"visible={visible}  activity={activity}  concern={concern}"
                )
                if concern in ("medium", "high"):
                    logger.warning(log_msg)
                else:
                    logger.info(log_msg)

                # Attach raw response for debugging
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
