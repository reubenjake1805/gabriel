"""
Gabriel — API Server

FastAPI app with endpoints for chat, events, live frames, and status.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
from storage.database import EventDB
from api.chat import ChatHandler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    events_used: int
    frames: list[dict]
    is_realtime: bool


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(db: EventDB, capture_manager=None, analyzer=None, frame_store=None) -> FastAPI:
    """
    Create the FastAPI app with all endpoints.

    Args:
        db: The EventDB instance for querying events.
        capture_manager: The CaptureManager for live frames.
        analyzer: The VisionAnalyzer for on-demand analysis.
        frame_store: The FrameStore for saving frames.
    """
    app = FastAPI(
        title="Gabriel",
        description="AI-powered cat monitoring API",
        version="0.1.0",
    )

    chat_handler = ChatHandler(db, capture_manager, analyzer, frame_store)

    # ------------------------------------------------------------------
    # POST /api/chat — Ask Gabriel a question
    # ------------------------------------------------------------------

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest):
        """Ask Gabriel a question about Lee."""
        if not request.question.strip():
            raise HTTPException(status_code=400, detail="Question cannot be empty")

        logger.info(f"Chat question: {request.question}")
        result = chat_handler.ask(request.question)
        logger.info(f"Chat answer: {result['answer'][:100]}...")
        return result

    # ------------------------------------------------------------------
    # GET /api/events — Query raw events
    # ------------------------------------------------------------------

    @app.get("/api/events")
    async def get_events(
        since: str = None,
        until: str = None,
        activity: str = None,
        concern_level: str = None,
        camera: str = None,
        limit: int = 100,
    ):
        """Query events from the database with optional filters."""
        events = db.get_events(
            since=since,
            until=until,
            activity=activity,
            concern_level=concern_level,
            camera=camera,
            limit=min(limit, 500),
        )
        return {"events": events, "count": len(events)}

    # ------------------------------------------------------------------
    # GET /api/live — Get the latest camera frame
    # ------------------------------------------------------------------

    @app.get("/api/live")
    async def get_live_frame(camera: str = None):
        """Get the most recent frame from a camera."""
        if not capture_manager:
            raise HTTPException(
                status_code=503,
                detail="Camera not available",
            )

        frame = capture_manager.get_latest_frame(camera)
        if frame is None:
            raise HTTPException(
                status_code=404,
                detail="No frame available",
            )

        # Save the live frame to a temp location and serve it
        import cv2
        live_path = config.FRAMES_DIR / "live"
        live_path.mkdir(parents=True, exist_ok=True)

        camera_name = camera or "default"
        filepath = live_path / f"{camera_name}_latest.jpg"
        cv2.imwrite(
            str(filepath),
            frame.image,
            [cv2.IMWRITE_JPEG_QUALITY, config.FRAME_JPEG_QUALITY],
        )

        return {
            "frame_url": str(filepath),
            "captured_at": frame.timestamp.isoformat(),
            "camera": frame.camera_name,
        }

    # ------------------------------------------------------------------
    # GET /api/status — System status
    # ------------------------------------------------------------------

    @app.get("/api/status")
    async def get_status():
        """Get system status and today's summary."""
        summary = db.get_today_summary()
        latest = db.get_latest_event()

        cameras_active = 0
        if capture_manager:
            cameras_active = len([
                c for c in capture_manager.cameras.values()
                if c.is_running
            ])

        return {
            "status": "running",
            "cameras_active": cameras_active,
            "total_events": db.get_event_count(),
            "today": summary,
            "latest_event": {
                "timestamp": latest["timestamp"] if latest else None,
                "activity": latest["activity"] if latest else None,
                "activity_detail": latest["activity_detail"] if latest else None,
                "concern_level": latest["concern_level"] if latest else None,
            } if latest else None,
        }

    # ------------------------------------------------------------------
    # GET /api/frames/{path} — Serve a frame file
    # ------------------------------------------------------------------

    @app.get("/api/frames/{date}/{filename}")
    async def serve_frame(date: str, filename: str):
        """Serve a saved frame JPEG."""
        filepath = config.FRAMES_DIR / date / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Frame not found")
        return FileResponse(str(filepath), media_type="image/jpeg")

    # ------------------------------------------------------------------
    # GET /api/audio/{date}/{filename} — Serve an audio clip
    # ------------------------------------------------------------------

    @app.get("/api/audio/{date}/{filename}")
    async def serve_audio(date: str, filename: str):
        """Serve a saved audio WAV clip."""
        audio_dir = config.BASE_DIR / "audio"
        filepath = audio_dir / date / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Audio clip not found")
        return FileResponse(str(filepath), media_type="audio/wav")

    return app
