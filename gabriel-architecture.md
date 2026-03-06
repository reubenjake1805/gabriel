# Gabriel — Architecture Document

**A chat app that lets you ask questions about your cat Lee, answered using live video analysis.**

*Version 1.2 — March 2026*

---

## 1. Overview

Gabriel is a system that continuously monitors your home via camera, builds a structured log of your cat Lee's activities using AI vision analysis, and exposes a natural-language chat interface where you and your wife can ask questions like "Has Lee eaten today?" or "Is Lee okay?"

The system is designed around three core principles:

- **Cost efficiency**: Only send frames to the vision API when something meaningful has changed.
- **Privacy**: All raw footage stays on your local machine. Only individual frames leave your network (sent to the vision API), and only structured text events are stored in the cloud.
- **Simplicity**: Prefer boring, reliable technology. SQLite over Postgres. Polling over websockets. One repo, minimal infrastructure.

---

## 2. Hardware

| Component | Current (Phase 1) | Future (Phase 2+) |
|---|---|---|
| Camera(s) | MacBook Pro (M4) built-in webcam | 1–3x TP-Link Tapo TC70 via RTSP |
| Local server | Same MacBook Pro | Same MacBook Pro |
| Storage | MacBook SSD | MacBook SSD |
| SD cards | N/A | SanDisk High Endurance 64GB (one per camera) |

The MacBook serves double duty as both camera and local server in Phase 1. In Phase 2, it becomes a dedicated server that pulls RTSP streams from the TC70 cameras.

---

## 3. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       MacBook Pro (M4)                            │
│                                                                   │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐       │
│  │   Camera     │    │  Frame       │    │  Gemini        │       │
│  │   Capture    │───▶│  Filter      │───▶│  Vision        │       │
│  │   + Ring     │    │  Pipeline    │    │  Analyzer      │       │
│  │   Buffer     │    │  + Burst     │    └───────┬────────┘       │
│  └──────┬──────┘    └──────────────┘            │                │
│         │                                  ┌─────▼─────┐         │
│         │                                  │  SQLite   │         │
│         │                                  │  Event    │         │
│         │                                  │  Log      │         │
│         │                                  └──┬────┬───┘         │
│         │                                     │    │             │
│         │    ┌────────────────────┐            │    │             │
│         └───▶│  Alert Dispatcher  │◀───────────┘    │             │
│              │  (Telegram bot)    │                  │             │
│              └────────┬───────────┘                  │             │
│                       │                              │             │
│  ┌────────────────────│──────────────────────────────▼──────────┐ │
│  │                    │    Gabriel API Server                    │ │
│  │                    │   (Session aggregation + Claude chat)    │ │
│  └────────────────────│─────────────────────────────────┬───────┘ │
│                       │                                 │         │
└───────────────────────│─────────────────────────────────┼─────────┘
                        │                                 │
           ┌────────────▼──────┐             ┌────────────▼────────────┐
           │  Telegram alerts   │             │    Mobile Chat App       │
           │  (your phones)     │             │    (React Native/Expo)   │
           └───────────────────┘             │    You & your wife       │
                                             └─────────────────────────┘
```

---

## 4. Component Details

### 4.1 Camera Capture Module

**Responsibility**: Acquire frames from the video source.

**Phase 1 (webcam)**:
```python
cap = cv2.VideoCapture(0)
```

**Phase 2 (RTSP)**:
```python
cameras = {
    "feeding_station": "rtsp://user:pass@192.168.1.10/stream1",
    "living_room":     "rtsp://user:pass@192.168.1.11/stream1",
    "litter_area":     "rtsp://user:pass@192.168.1.12/stream1",
}
```

The module captures one raw frame per second (configurable) and passes it to the filter pipeline.

For RTSP cameras, each camera runs in its own thread. The module handles reconnection automatically if a camera drops off the network.

**Ring buffer**: The capture module maintains a rolling in-memory buffer of the last `RING_BUFFER_SECONDS` seconds of raw frames (default: 30). At 1 fps, this is just 30 frames (~15 MB of memory) — trivial for an M4 MacBook. Under normal operation, old frames silently roll off the end of the buffer and are never saved.

The ring buffer exists for one reason: **concern event context**. When the vision analyzer flags an event with `concern_level` of "medium" or "high", the alert dispatcher (Section 4.7) flushes the entire ring buffer to disk alongside the event. This gives you a "what happened" filmstrip — the 30 seconds leading up to the concern, plus the frames that follow. Without this, you'd only have the single frame that triggered the alert, which might be blurry or ambiguous.

```python
# Ring buffer is a simple collections.deque
from collections import deque
ring_buffer = deque(maxlen=RING_BUFFER_SECONDS)  # auto-evicts oldest frames
```

**Key config**:
- `CAPTURE_INTERVAL`: How often to grab a raw frame (default: 1 second)
- `CAMERA_SOURCES`: Dict mapping camera names to sources (device index or RTSP URL)
- `RING_BUFFER_SECONDS`: How many seconds of frames to keep in memory (default: 30)

---

### 4.2 Frame Filter Pipeline

**Responsibility**: Decide which frames are worth sending to the vision API.

This is the most important module for cost control. It implements a three-tier filtering strategy:

#### Tier 1 — Motion Detection (local, free)

Compares each frame to the previous frame using OpenCV's `absdiff`. Converts both to grayscale, computes the absolute pixel difference, applies a threshold, and counts the percentage of changed pixels. If the change is below `MOTION_THRESHOLD`, the frame is discarded.

```
current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
diff = cv2.absdiff(current_gray, previous_gray)
_, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
motion_pct = (cv2.countNonZero(thresh) / thresh.size) * 100
```

**Tuning**: `MOTION_THRESHOLD` defaults to 2.0 (percent of pixels changed). Too low = too many false triggers from lighting changes. Too high = misses Lee walking slowly. You'll tune this after observing a day of logs.

#### Tier 2 — Perceptual Deduplication (local, free)

Even when motion is detected, the scene might not have meaningfully changed (e.g., Lee's tail flicking while he naps). We compute a perceptual hash (pHash) of each frame that passes Tier 1 and compare it to the last frame we actually sent to the vision API.

```
hash_current = imagehash.phash(Image.fromarray(frame))
distance = hash_current - last_analyzed_hash
```

If the Hamming distance is below `DEDUP_THRESHOLD`, the frame is considered a duplicate and skipped.

**Tuning**: `DEDUP_THRESHOLD` defaults to 12 (out of 64 bits). Lower = more aggressive dedup (fewer API calls, might miss subtle changes). Higher = more permissive (more API calls, catches more detail).

#### Tier 3 — Heartbeat (forced periodic sample)

Regardless of motion or deduplication, one frame is force-sent to the vision API every `HEARTBEAT_INTERVAL` seconds (default: 900 = 15 minutes). This ensures the event log always has recent entries, even if Lee has been sleeping for hours. Without this, Gabriel would have nothing to say when you ask "Is Lee okay?" during a quiet afternoon.

#### Filter Decision Logic

```
for each captured frame:
    if extreme_motion_detected(frame):               # >BURST_MOTION_THRESHOLD
        → enter BURST MODE (see below)
    else if motion_detected(frame):
        if not duplicate(frame):
            → send to vision API (activity frame)
            update last_analyzed_hash
    else if time_since_last_analysis > HEARTBEAT_INTERVAL:
        → send to vision API (heartbeat frame)
        update last_analyzed_hash
    else:
        → discard
```

#### Burst Mode — Handling Sudden Events

Normal filtering is designed for everyday activities: Lee walking, eating, napping. But sudden events — a fall, a collision, knocking something heavy over — produce a spike in pixel change that's far above the normal motion threshold. When this happens, a single analyzed frame might not tell the full story (it could be blurry or mid-action).

When the motion score exceeds `BURST_MOTION_THRESHOLD` (default: 15% of pixels changed, roughly 7x the normal threshold), the pipeline enters burst mode:

1. **Increase the capture rate** from the normal 1 fps to `BURST_FPS` (default: 4 fps) for the duration of the burst window
2. **Skip the dedup filter entirely** for the duration of the burst
3. **Save ALL high-framerate frames to disk** (for manual review later)
4. **Send a sampled subset to Gemini** — every Nth frame, totalling roughly `BURST_ANALYZE_COUNT` frames (default: 6). This keeps API costs in check while still giving Gemini multiple perspectives on the event.
5. **If ANY analyzed frame returns `concern_level` of "medium" or "high"**, trigger the alert dispatcher (Section 4.7)
6. **After `BURST_DURATION_SECONDS` (default: 5)**, resume normal capture rate and filtering

The key insight is that the capture rate increase and the API analysis are decoupled. At 4 fps for 5 seconds, we capture 20 frames and save all of them to disk. But we only send ~6 of those to Gemini (every 3rd or 4th frame). This gives you a high-resolution filmstrip of the event for manual review, while keeping the API cost to just ~5 extra calls per burst.

```
Burst mode example — Lee falls off railing:

14:32:00.00  Normal frame: Lee on railing           → normal pipeline, logged
14:32:01.00  EXTREME MOTION (42% pixel change)      → BURST MODE activated (4 fps)
14:32:01.00  Burst frame 1:  Lee tipping            → saved to disk, sent to Gemini
14:32:01.25  Burst frame 2:  Lee mid-fall           → saved to disk
14:32:01.50  Burst frame 3:  Lee mid-fall           → saved to disk
14:32:01.75  Burst frame 4:  Lee mid-fall           → saved to disk, sent to Gemini
14:32:02.00  Burst frame 5:  Lee hitting ground     → saved to disk
14:32:02.25  Burst frame 6:  Lee on ground          → saved to disk
14:32:02.50  Burst frame 7:  Lee on ground          → saved to disk, sent to Gemini → concern: HIGH
14:32:02.75  Burst frame 8:  Lee lying still        → saved to disk
14:32:03.00  Burst frame 9:  Lee lying still        → saved to disk
14:32:03.25  Burst frame 10: Lee lying still        → saved to disk, sent to Gemini → concern: HIGH
14:32:03.50  Burst frame 11: Lee starting to move   → saved to disk
14:32:03.75  Burst frame 12: Lee starting to move   → saved to disk
14:32:04.00  Burst frame 13: Lee moving             → saved to disk, sent to Gemini → concern: MEDIUM
14:32:04.25  Burst frame 14: Lee standing           → saved to disk
14:32:04.50  Burst frame 15: Lee standing           → saved to disk
14:32:04.75  Burst frame 16: Lee walking            → saved to disk, sent to Gemini → concern: LOW
...
14:32:06.00  BURST MODE ends → alert triggered, ring buffer + all burst frames saved
             Normal capture rate (1 fps) resumes
```

**Expected daily volume** (10 hours, one camera):
- Raw frames captured: ~36,000 (1/sec)
- After Tier 1 (motion): ~2,000–5,000
- After Tier 2 (dedup): ~80–120
- Heartbeat frames: ~30–40
- Burst mode frames: ~0–15 (rare, only on dramatic events)
- **Total frames sent to API: ~100–170**

---

### 4.3 Gemini Vision Analyzer

**Responsibility**: Look at a frame and produce a structured JSON description of what Lee is doing.

**Model**: Gemini 2.0 Flash (cheapest vision model with good accuracy for this task).

**API call structure**:

For each frame that passes the filter pipeline, we send a request to Gemini with:

1. The frame as a base64-encoded JPEG image
2. A system prompt tailored to Lee and your home setup
3. A request for structured JSON output

**System prompt**:
```
You are an AI assistant analyzing security camera footage of a home where
a 10-month-old cat named Rock Lee (called "Lee") lives. Lee is a domestic shorthair tabby with
a greyish coat and a pink nose. The home has an automatic feeder and 2 litter boxes.

Analyze this camera frame and respond with ONLY a JSON object:

{
  "lee_visible": boolean,
  "lee_location": string or null,    // e.g. "on the couch", "near the feeder", "by the window"
  "activity": string,                // one of: "eating", "drinking", "sleeping", "playing",
                                     //   "grooming", "using_litter_box", "exploring",
                                     //   "resting", "looking_outside", "running",
                                     //   "not_visible", "other"
  "activity_detail": string,         // free-text description, e.g. "Lee is eating from the
                                     //   automatic feeder, bowl appears half full"
  "posture": string or null,         // e.g. "curled up", "stretched out", "sitting upright",
                                     //   "crouching"
  "energy_level": string,            // "low", "medium", "high"
  "concern_level": "none" | "low" | "medium" | "high",
  "concern_detail": string or null,  // only if concern_level > none, e.g. "Lee appears to
                                     //   be limping on front left paw"
  "environment_notes": string or null // anything notable about the room — lights on/off,
                                     //   objects knocked over, etc.
}
```

**Why structured JSON?** This makes the event log trivially queryable. "How many meals has Lee eaten?" becomes a SQL query filtering on `activity = 'eating'` and grouping by time window.

**Error handling**: If Gemini returns malformed JSON (rare but possible), we retry once. If it fails again, we log the raw response as a text event with `activity = "analysis_failed"` and move on. We never block the pipeline.

**Cost estimate**: At ~100–150 frames/day with Gemini 2.0 Flash vision pricing, expect roughly $0.10–0.30/day.

---

### 4.4 SQLite Event Log

**Responsibility**: Store all analyzed events in a queryable database.

**Why SQLite?** It runs on your MacBook with zero setup, handles the write volume easily (150 inserts/day is nothing), and the database is a single file you can back up by copying it. No need for Postgres, no need for a cloud database at this stage.

**Schema**:

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,           -- ISO 8601, e.g. "2026-03-01T14:32:00+05:30"
    camera TEXT NOT NULL,              -- "webcam", "feeding_station", "living_room", etc.
    frame_type TEXT NOT NULL,          -- "motion", "heartbeat", or "burst"
    lee_visible INTEGER NOT NULL,      -- 0 or 1
    lee_location TEXT,
    activity TEXT NOT NULL,
    activity_detail TEXT,
    posture TEXT,
    energy_level TEXT,
    concern_level TEXT NOT NULL DEFAULT 'none',
    concern_detail TEXT,
    environment_notes TEXT,
    frame_path TEXT,                   -- path to saved JPEG on disk (for review if needed)
    context_frames_dir TEXT,           -- path to ring buffer dump (only for concern events)
    raw_response TEXT,                 -- full Gemini response (for debugging)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_events_timestamp ON events(timestamp);
CREATE INDEX idx_events_activity ON events(activity);
CREATE INDEX idx_events_concern ON events(concern_level);
```

**Frame storage**: Each analyzed frame is saved as a JPEG on disk in a rolling directory structure:

```
~/gabriel/frames/
  2026-03-01/
    webcam_143200_motion.jpg
    webcam_144500_heartbeat.jpg
    ...
```

Frames older than 48 hours are auto-deleted by a cleanup job. The event log entries persist indefinitely (they're tiny — a few KB each), but the actual images rotate out.

---

### 4.5 Gabriel API Server

**Responsibility**: Accept natural-language questions from the mobile app and return answers based on the event log.

**Tech**: Python (FastAPI), running on the MacBook.

**How it works**:

1. Receive a question from the mobile app (e.g., "How many meals has Lee eaten today?")
2. Pull relevant events from SQLite based on time range (default: today) and optionally filter by activity type
3. Pass the events + the user's question to Claude (Sonnet) with a system prompt
4. Return Claude's natural-language answer to the app

**Why Claude for the chat layer?** Claude is better at synthesizing multiple data points into a warm, conversational answer. When you ask "Is Lee okay?", you don't want a database dump — you want something like: "Lee's been having a good day! He ate his morning meal around 9:15 AM and has been napping on the couch most of the afternoon. No concerns at all — he looks comfortable and relaxed."

**Query pre-processing**: Before sending events to Claude, the server applies intelligence to reduce token usage and improve answer quality:

- **Time filtering**: Only include events from the relevant time window (today, last 2 hours, etc.)
- **Session aggregation**: Consecutive events with the same `activity` that are less than `SESSION_GAP` minutes apart (default: 10) are collapsed into a single **session**. This is critical for natural answers — without it, 8 frames of Lee grooming would be reported as "Lee groomed himself 8 times" instead of "Lee had an 18-minute grooming session."

  ```
  Raw events:
    14:00  grooming  "Lee is grooming his front paws"
    14:02  grooming  "Lee is licking his side"
    14:05  grooming  "Lee is grooming his back leg"
    14:09  grooming  "Lee is grooming his chest"
    14:12  grooming  "Lee is grooming his tail"
    14:18  grooming  "Lee is grooming his front paws again"
    14:22  sleeping  "Lee is curled up, appears to have fallen asleep"

  After session aggregation:
    14:00–14:18  grooming (6 events, ~18 min)
      Details: "front paws, side, back leg, chest, tail, front paws again"
    14:22        sleeping
      Details: "curled up, appears to have fallen asleep"
  ```

  The aggregation logic: iterate through events in chronological order. If the current event has the same `activity` as the previous one and the gap between them is less than `SESSION_GAP`, merge them into the same session. Otherwise, start a new session. Each session records: start time, end time, activity, event count, duration, and a concatenation of the unique `activity_detail` values.

- **Priority sorting**: Concern events are always included in full (never aggregated away). Eating and litter box events are always included. Sleeping sessions are summarized to just start/end time and duration.
- **Token budgeting**: The total event summary sent to Claude is capped at ~2,000 tokens. If there are too many events in a day, older low-priority sessions (e.g., "not_visible") are dropped first.

**System prompt for the chat model**:
```
You are Gabriel, a friendly and reassuring AI assistant that helps monitor
a 10-month-old cat named Rock Lee (called "Lee"). You answer questions from
Lee's owners based on the event log data provided.

Guidelines:
- Be warm and conversational, not clinical.
- When Lee is doing well (which is most of the time), be reassuring.
- If there are any concerns in the log, mention them clearly but calmly.
- If you don't have enough data to answer confidently, say so honestly.
- Refer to specific times when relevant ("Lee ate around 9:15 AM").
- You can reference Lee's personality and habits as you learn them over time.
- Never make up events that aren't in the log.
```

**Endpoints**:

```
POST /api/chat
  Body: { "question": "Has Lee eaten today?" }
  Response: {
    "answer": "Yes! Lee had his morning meal...",
    "events_used": 3,
    "frames": [
      {
        "timestamp": "2026-03-01T09:14:32+05:30",
        "url": "/frames/2026-03-01/webcam_091432_motion.jpg",
        "activity": "eating"
      },
      {
        "timestamp": "2026-03-01T13:02:10+05:30",
        "url": "/frames/2026-03-01/webcam_130210_motion.jpg",
        "activity": "eating"
      }
    ]
  }

GET /api/live?camera=webcam
  Response: {
    "frame_url": "/frames/live/webcam_latest.jpg",
    "captured_at": "2026-03-01T15:42:01+05:30"
  }

GET /api/events?since=2026-03-01T00:00:00&activity=eating
  Response: [{ event }, { event }, ...]

GET /api/status
  Response: { "last_event": "...", "cameras_active": 1, "pipeline_running": true }
```

**Frame retrieval in chat responses**: The `/api/chat` endpoint returns a `frames` array containing the frames associated with the events Claude used to generate its answer. The mobile app renders these as tappable thumbnails below the message bubble — tapping opens the full-resolution image. This gives visual confirmation alongside the text answer ("Lee ate at 9:15 AM" + a photo of Lee at the feeder).

Since frames auto-delete after 48 hours but events persist forever, the app handles missing frames gracefully: if a frame file no longer exists, the `url` field is returned as `null` and the app simply shows the text answer without a thumbnail.

**Live frame endpoint**: The `/api/live` endpoint serves the most recent frame directly from the ring buffer — no Gemini call, no event logging, no filtering. It's essentially a low-latency "what does the camera see right now?" snapshot. This is cheap and fast (just reading from memory).

**"What is Lee doing right now?" flow**: When the chat layer detects a question about Lee's current state (keywords like "right now", "at the moment", "currently", "doing now"), it does two things in parallel:

1. Grabs the latest frame from the ring buffer and includes it in the response
2. Sends that same frame to Gemini for a fresh on-demand analysis, so the text answer reflects what's happening *now* rather than whatever the last logged event was (which could be up to 15 minutes old if it was a heartbeat during a quiet period)

This costs one extra Gemini API call per "right now" question — negligible given these are infrequent.

**Exposing the server to the internet**: Since the API runs on your MacBook at home but you need to reach it from your phones at work, you have a few options:

- **Cloudflare Tunnel (recommended)**: Free, no port forwarding, secure. Install `cloudflared` on your Mac and it creates a tunnel to a public URL.
- **Tailscale**: Creates a private network between your Mac and phones. No public exposure.
- **ngrok**: Quick and easy for testing, but the free tier URL changes on restart.

---

### 4.7 Alert Dispatcher

**Responsibility**: Proactively notify you and your wife when something concerning happens, without waiting for you to ask.

The alert dispatcher watches for two types of triggers:

#### Trigger 1: Concern Events

When the vision analyzer returns an event with `concern_level` of "medium" or "high", the dispatcher:

1. **Flushes the ring buffer** to disk, saving the last 30 seconds of frames as a "context filmstrip" alongside the event. These are stored in a subdirectory:
   ```
   ~/gabriel/frames/2026-03-01/
     concern_143201/
       context_143130.jpg    ← 30 sec before
       context_143135.jpg
       ...
       trigger_143201.jpg    ← the frame that triggered the alert
       after_143202.jpg      ← frames captured after the trigger
       after_143203.jpg
       ...
   ```

2. **Sends a push notification** to your phones with a brief summary:
   > **Gabriel Alert** 🐱
   > Lee may have fallen at 2:32 PM. He was on the staircase railing and is now on the ground floor. Please check the app for details.

3. **Logs the alert** in a separate `alerts` table so you can review them later.

#### Trigger 2: Inactivity Alerts

If no events with `lee_visible: true` have been logged in `INACTIVITY_ALERT_HOURS` hours (default: 4), the dispatcher sends a gentler notification:

> **Gabriel** 🐱
> I haven't seen Lee in about 4 hours. He might be in a spot the camera can't see, but you may want to check in.

This catches edge cases where Lee might be stuck somewhere or unwell but out of camera view.

#### Notification Channel

For Phase 1, we'll use a **Telegram bot** for notifications. It's the simplest option that doesn't require building push notification infrastructure into the mobile app:

- Create a Telegram bot via BotFather (takes 2 minutes)
- Add yourself and your wife to a group chat with the bot
- The dispatcher sends messages to that group chat via the Telegram Bot API

This can be upgraded to Firebase Cloud Messaging (FCM) in a later phase for native push notifications in the mobile app.

**Key config**:
- `ALERT_ENABLED`: Toggle alerts on/off (default: true)
- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
- `TELEGRAM_CHAT_ID`: The group chat ID
- `INACTIVITY_ALERT_HOURS`: Hours of no Lee sightings before alerting (default: 4)
- `ALERT_COOLDOWN_MINUTES`: Minimum gap between alerts to avoid spam (default: 5)

---

### 4.8 Mobile Chat App

**Responsibility**: Provide a simple chat interface for you and your wife.

**Tech**: React Native with Expo (cross-platform, builds for both iOS and Android).

**Features**:
- Simple chat UI — text input, message bubbles
- Send questions, receive Gabriel's answers
- **Frame thumbnails**: Chat responses include tappable thumbnails of the frames Gabriel used to answer your question. Tap to view full-resolution. If frames have expired (>48 hours), the text answer is shown without thumbnails.
- **Live view**: A "See Lee now" button that calls `/api/live` and shows the latest camera frame. No AI analysis — just a quick snapshot for peace of mind.
- Push notifications via Telegram (Phase 1), native FCM (Phase 2)
- Both you and your wife can use it simultaneously (the API is stateless)

**Scope for Phase 1**: The app is intentionally minimal. No live video streaming, no complex settings. A chat box that talks to Gabriel, with frame thumbnails on responses and a live snapshot button. We can add bells and whistles later.

---

## 5. Data Flow — End to End

Here's what happens when you ask Gabriel "Has Lee eaten today?" at 3 PM from your phone at work:

```
1. Your phone sends POST /api/chat { "question": "Has Lee eaten today?" }
      │
      ▼
2. Gabriel API server (on MacBook) receives the request
      │
      ▼
3. Server queries SQLite:
   SELECT * FROM events
   WHERE timestamp >= '2026-03-01T00:00:00'
     AND activity = 'eating'
   ORDER BY timestamp
      │
      ▼
4. Finds 2 events:
   - 09:14 AM: "Lee is eating from the automatic feeder, bowl appears half full"
     → frame_path: ~/gabriel/frames/2026-03-01/webcam_091432_motion.jpg
   - 01:02 PM: "Lee is eating from the feeder again, appears to be finishing his meal"
     → frame_path: ~/gabriel/frames/2026-03-01/webcam_130210_motion.jpg
      │
      ▼
5. Server applies session aggregation, then sends to Claude:
   System: [Gabriel system prompt]
   User: "The user asks: 'Has Lee eaten today?'
          Here are the relevant events from today:
          [09:14] eating — Lee is eating from the automatic feeder...
          [13:02] eating — Lee is eating from the feeder again..."
      │
      ▼
6. Claude responds:
   "Yes! Lee has eaten twice today. He had his first meal around 9:15 AM and
    came back for a second helping around 1 PM. Looks like he's got a healthy
    appetite today!"
      │
      ▼
7. Server returns response + frame URLs to your phone:
   {
     "answer": "Yes! Lee has eaten twice today...",
     "events_used": 2,
     "frames": [
       { "timestamp": "09:14", "url": "/frames/..._091432_motion.jpg", "activity": "eating" },
       { "timestamp": "13:02", "url": "/frames/..._130210_motion.jpg", "activity": "eating" }
     ]
   }
      │
      ▼
8. You see the answer in the chat app with tappable photo thumbnails
   of Lee at the feeder — and feel reassured 🐱
```

---

## 6. Configuration

All configuration lives in a single `config.py` file:

```python
# --- Camera ---
CAMERA_SOURCES = {
    "webcam": 0,  # Phase 1: device index
    # "feeding_station": "rtsp://user:pass@192.168.1.10/stream1",  # Phase 2
}

# --- Frame Capture ---
CAPTURE_INTERVAL = 1.0          # seconds between raw frame grabs
RING_BUFFER_SECONDS = 30        # seconds of frames kept in memory for concern context

# --- Filter Pipeline ---
MOTION_THRESHOLD = 2.0          # % of pixels changed to trigger motion
DEDUP_THRESHOLD = 12            # pHash Hamming distance threshold
HEARTBEAT_INTERVAL = 900        # seconds (15 min) between forced samples
BURST_MOTION_THRESHOLD = 15.0   # % of pixels changed to trigger burst mode
BURST_FPS = 4                   # capture rate during burst mode (normal: 1 fps)
BURST_DURATION_SECONDS = 5      # how long burst mode lasts
BURST_ANALYZE_COUNT = 6         # number of burst frames sent to Gemini (rest saved to disk only)

# --- Vision API ---
GEMINI_API_KEY = "your-key-here"
GEMINI_MODEL = "gemini-2.0-flash"
FRAME_JPEG_QUALITY = 85         # balance between quality and upload size

# --- Chat API ---
ANTHROPIC_API_KEY = "your-key-here"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
SESSION_GAP = 10                # minutes — events closer than this are one session

# --- Alerts ---
ALERT_ENABLED = True
TELEGRAM_BOT_TOKEN = "your-token-here"
TELEGRAM_CHAT_ID = "your-chat-id-here"
INACTIVITY_ALERT_HOURS = 4      # alert if Lee not seen for this long
ALERT_COOLDOWN_MINUTES = 5      # minimum gap between alerts

# --- Storage ---
DB_PATH = "~/gabriel/gabriel.db"
FRAMES_DIR = "~/gabriel/frames"
FRAME_RETENTION_HOURS = 48      # auto-delete frames older than this

# --- Server ---
API_HOST = "0.0.0.0"
API_PORT = 8080
```

---

## 7. Project Structure

```
gabriel/
├── config.py                  # All configuration
├── main.py                    # Entry point — starts capture + API server
├── capture/
│   ├── __init__.py
│   ├── camera.py              # Camera capture module (webcam + RTSP) + ring buffer
│   └── filters.py             # Motion detection, dedup, heartbeat, burst mode
├── audio/                     # Phase 2
│   ├── __init__.py
│   ├── capture.py             # Audio stream capture (FFmpeg / PyAudio)
│   ├── classifier.py          # YAMNet local sound classification
│   └── analyzer.py            # Gemini audio analysis (for ambiguous clips)
├── analysis/
│   ├── __init__.py
│   └── vision.py              # Gemini API calls + JSON parsing
├── storage/
│   ├── __init__.py
│   ├── database.py            # SQLite event log (schema, insert, query)
│   └── frames.py              # Frame file management + cleanup
├── api/
│   ├── __init__.py
│   ├── server.py              # FastAPI app
│   ├── chat.py                # Chat endpoint (Claude integration)
│   ├── sessions.py            # Session aggregation logic
│   └── events.py              # Events query endpoint
├── alerts/
│   ├── __init__.py
│   └── dispatcher.py          # Concern alerts + inactivity alerts + Telegram
├── app/                       # React Native mobile app (Phase 2)
│   └── ...
└── requirements.txt
```

---

## 8. Technology Stack Summary

| Layer | Technology | Reason |
|---|---|---|
| Frame capture | OpenCV (cv2) | Industry standard, works with webcam + RTSP |
| Motion detection | OpenCV absdiff | Simple, fast, no dependencies |
| Perceptual hashing | ImageHash (Python) | Lightweight, well-maintained |
| Vision analysis | Gemini 2.0 Flash | Cheapest vision API, good enough for the task |
| Event storage | SQLite | Zero setup, single file, plenty fast |
| API server | FastAPI (Python) | Fast, async, great for small APIs |
| Chat synthesis | Claude Sonnet | Best at warm, conversational summarization |
| Alert notifications | Telegram Bot API | Free, instant, no infra needed |
| Mobile app | React Native + Expo | Cross-platform, fast to prototype |
| Tunnel to internet | Cloudflare Tunnel | Free, secure, stable URLs |

---

## 9. Build Order

The recommended order for implementation:

1. **Camera capture + frame filter pipeline** — Get frames flowing, verify motion detection, dedup, and burst mode are working. Test by pointing at Lee and watching the logs.
2. **Gemini vision analyzer** — Send filtered frames to Gemini, verify you get clean JSON back. Log results to console.
3. **SQLite event log** — Wire up storage. Verify events are being inserted correctly.
4. **Gabriel API server + session aggregation + Claude chat** — Build the query endpoint with session collapsing. Test by asking questions via curl.
5. **Alert dispatcher** — Set up Telegram bot. Verify concern alerts and inactivity alerts work.
6. **Tunnel setup** — Expose the API to the internet via Cloudflare Tunnel.
7. **Mobile chat app** — Build the minimal chat UI. Connect to the API.
8. **Swap webcam for TC70** — When the camera arrives, change the source config from device index to RTSP URL. Everything else stays the same.

---

## 10. Cost Estimates (Monthly)

| Item | Cost |
|---|---|
| Gemini Flash vision (~150 frames/day) | ₹250–750/month |
| Claude Sonnet chat (~10 queries/day) | ₹150–400/month |
| Cloudflare Tunnel | Free |
| Cloud infrastructure | ₹0 (everything runs locally) |
| **Total** | **~₹400–1,150/month** |

---

## 11. Audio Monitoring (Phase 2)

The current architecture is vision-only. Every piece of information Gabriel has about Lee comes from analyzing video frames. This means there's a significant category of events Gabriel cannot detect today:

- **Excessive meowing or crying** — one of the earliest signs of pain, illness, hunger, or distress in cats
- **Vomiting or retching** — has a very distinct sound, often happens off-camera or in another room
- **Thuds or crashes** — Lee knocking something heavy over, or falling, especially when out of camera view
- **Hissing or growling** — relevant if a second pet is ever introduced
- **Scratching at doors** — wanting to enter or leave a room
- **Silence when it shouldn't be silent** — e.g., Lee usually meows around feeding time but hasn't today

Audio is especially valuable because it's **omnidirectional** — a camera only sees what it's pointed at, but a microphone picks up events from anywhere in the room, including behind furniture, around corners, or in adjacent rooms. A cat vomiting behind the couch is invisible to the camera but clearly audible.

### 11.1 Hardware

No additional hardware is needed. The TP-Link Tapo TC70 has a built-in microphone, and the audio channel is included in the RTSP stream. It can be captured separately from the video using FFmpeg:

```bash
# Extract audio channel from RTSP stream
ffmpeg -i rtsp://user:pass@192.168.1.10/stream1 -vn -acodec pcm_s16le -ar 16000 -ac 1 -f segment -segment_time 10 audio_chunk_%04d.wav
```

This gives us a continuous stream of 10-second audio chunks at 16kHz mono — the standard input format for most audio classification models.

For Phase 1 (MacBook webcam), the MacBook's built-in microphone can serve the same role for development and testing.

### 11.2 Audio Pipeline Architecture

The audio pipeline runs **parallel to** the vision pipeline, not inside it. It has its own capture, filtering, and analysis stages:

```
┌─────────────────────────────────────────────────────────────┐
│                     Audio Pipeline                           │
│                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐  │
│  │   Audio      │    │  Sound       │    │  Gemini Audio  │  │
│  │   Capture    │───▶│  Classifier  │───▶│  Analyzer      │  │
│  │   (FFmpeg)   │    │  (YAMNet)    │    │  (if needed)   │  │
│  └─────────────┘    └──────────────┘    └───────┬────────┘  │
│                                                  │           │
│                                            ┌─────▼─────┐    │
│                                            │  SQLite   │    │
│                                            │  Event    │    │
│                                            │  Log      │    │
│                                            └───────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 11.3 Two-Tier Audio Filtering

Audio needs its own filtering strategy, analogous to the vision pipeline's three-tier approach. The goal is the same: avoid sending every second of audio to an API. Most of the day, the microphone picks up nothing interesting — ambient silence, traffic noise, TV, human conversation. We only want to flag sounds that are relevant to Lee.

#### Tier 1 — Local Sound Classification (YAMNet, free)

YAMNet is a pre-trained audio event classifier from Google, trained on AudioSet (632 sound classes). It runs locally on the MacBook, requires no API calls, and processes audio in real-time. Crucially, it already knows categories like:

- `Cat` / `Meow` / `Purr` / `Hiss` / `Caterwaul`
- `Retching` / `Vomiting`
- `Crash` / `Thud` / `Bang`
- `Scratching`
- `Glass breaking`

For each 10-second audio chunk, YAMNet outputs a list of detected sound classes with confidence scores. We filter for animal-related and concern-related sounds above a confidence threshold (tunable, default: 0.5).

```python
# YAMNet runs locally via TensorFlow Lite
import tensorflow_hub as hub
model = hub.load('https://tfhub.dev/google/yamnet/1')
scores, embeddings, spectrogram = model(audio_waveform)
# Filter for relevant sound classes
```

If YAMNet detects nothing relevant, the audio chunk is discarded. This should filter out 90%+ of all audio.

#### Tier 2 — AI Audio Analysis (Gemini, only when needed)

When YAMNet flags a relevant sound, we have two options depending on confidence:

**High confidence (>0.8)**: Log directly to the event database without an API call. If YAMNet is 90% sure it heard a cat meow, we don't need Gemini to confirm — we log it as an audio event with the YAMNet classification.

**Medium confidence (0.5–0.8)**: Send the audio clip to Gemini for confirmation and detailed analysis. Gemini 2.0 Flash supports audio input and can provide nuanced interpretation:

```json
{
  "source": "audio",
  "sound_detected": "cat_vocalization",
  "sound_detail": "Repeated short meows, sounds urgent/demanding — likely requesting food or attention",
  "duration_seconds": 4,
  "intensity": "medium",
  "concern_level": "low",
  "concern_detail": null
}
```

This two-tier approach means most audio processing happens locally for free, and only ambiguous clips hit the API — likely just 5–15 per day.

### 11.4 Audio Event Schema

Audio events are stored in the same `events` table as vision events, with the `source` field distinguishing them:

```sql
-- Additional columns for audio events (added to existing events table)
ALTER TABLE events ADD COLUMN source TEXT NOT NULL DEFAULT 'vision';  -- "vision" or "audio"
ALTER TABLE events ADD COLUMN sound_class TEXT;           -- YAMNet class: "meow", "crash", etc.
ALTER TABLE events ADD COLUMN sound_confidence REAL;      -- YAMNet confidence score (0.0–1.0)
ALTER TABLE events ADD COLUMN audio_clip_path TEXT;       -- path to saved audio clip
```

This means the chat layer and session aggregation work automatically with audio events. When you ask "Is Lee okay?", Claude will see both vision events ("Lee is sleeping on the couch") and audio events ("Lee meowed loudly 3 times around 2 PM") and synthesize them together.

### 11.5 Audio + Vision Correlation

The most powerful aspect of adding audio is **cross-modal correlation**. Some examples:

- Vision sees Lee near the litter box + audio detects retching → concern level elevated, Gabriel alerts you
- Vision shows Lee not visible for 2 hours + audio detects persistent meowing from a different room → Lee might be trapped somewhere
- Vision shows Lee sleeping peacefully + audio detects a loud crash → something fell, but Lee is fine (mention it but low concern)
- Audio detects meowing near feeding time + vision confirms Lee is near the feeder → "Lee is asking for food"

The session aggregation layer (Section 4.5) will merge audio and vision events from similar timestamps into unified sessions. For example, if within a 2-minute window we have a vision event of Lee near the feeder and an audio event of meowing, these become one session: "Lee was at the feeder, meowing — likely hungry."

### 11.6 Audio-Specific Alerts

Some audio events should trigger alerts independently of the vision pipeline:

- **Prolonged distress vocalization**: Continuous meowing or crying for more than `AUDIO_DISTRESS_MINUTES` (default: 5 minutes) → Telegram alert
- **Vomiting/retching sounds**: Any detection → immediate Telegram alert (even if low confidence — better to false-alarm than miss this)
- **Loud crash + no Lee visible on any camera**: → Telegram alert suggesting you check on Lee

### 11.7 Audio Configuration

```python
# --- Audio Pipeline (Phase 2) ---
AUDIO_ENABLED = False                   # Toggle audio pipeline on/off
AUDIO_CHUNK_SECONDS = 10                # Duration of each audio segment for classification
AUDIO_SAMPLE_RATE = 16000               # Hz (YAMNet expects 16kHz mono)
YAMNET_CONFIDENCE_THRESHOLD = 0.5       # Minimum confidence to consider a sound relevant
YAMNET_AUTO_LOG_THRESHOLD = 0.8         # Above this, log directly without Gemini confirmation
AUDIO_DISTRESS_MINUTES = 5             # Minutes of continuous vocalization before alerting
AUDIO_CLIP_RETENTION_HOURS = 48         # Auto-delete saved audio clips after this
```

### 11.8 Why Phase 2 and Not Phase 1

Audio adds significant complexity: a second capture pipeline, a local ML model (YAMNet + TensorFlow), audio-specific filtering logic, and cross-modal correlation. Getting the vision pipeline working first lets us validate the core architecture (event logging, session aggregation, chat synthesis, alerts) before layering on a second input modality. Once Phase 1 is stable and running reliably, adding audio becomes a matter of plugging a parallel pipeline into the same event log and alert dispatcher — the downstream components (Claude chat, Telegram alerts, session aggregation) already handle it.

---

## 12. Future Enhancements (Not in Phase 1)

- **Multi-camera support**: Add TC70 cameras, each with its own capture thread
- **Native push notifications**: Upgrade from Telegram bot to Firebase Cloud Messaging (FCM) for native mobile alerts
- **Daily digest**: Auto-generate a summary of Lee's day and send it to you and your wife in the evening
- **Activity trends**: Track patterns over weeks — eating times, sleep duration, activity levels
- **Voice queries**: Ask Gabriel questions via voice instead of typing
- **Live video streaming**: Full RTSP stream viewable in the app (vs current single-frame snapshots)
- **Concern filmstrip viewer**: In-app UI to scrub through the ring buffer context frames for a concern event
- **Multi-pet support**: Teach Gemini to distinguish Lee from any future pets
- **Audio monitoring**: See Section 11 — full audio pipeline with YAMNet + Gemini for detecting vocalizations, crashes, and distress sounds
