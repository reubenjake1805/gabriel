# Gabriel 🐱

**An AI-powered cat monitoring system that lets you ask natural-language questions about your cat — and get real answers backed by live video analysis.**

> "Has Lee eaten today?" → "Yes! Lee had his morning meal around 9:15 AM and came back for a second helping around 1 PM. Looks like he's got a healthy appetite today!"

Gabriel continuously watches your home via camera, builds a structured activity log using AI vision analysis, and exposes a chat interface where you can ask questions about your cat anytime, from anywhere.

---

## How It Works

1. **Camera captures frames** from your webcam or IP camera (1 fps)
2. **Smart filtering** reduces ~36,000 daily frames down to ~100–150 worth analyzing (motion detection → perceptual dedup → heartbeat)
3. **Gemini Flash** analyzes each selected frame and produces structured JSON: what your cat is doing, where, energy level, any concerns
4. **Events are logged** to a local SQLite database
5. **You ask questions** via a chat app on your phone
6. **Claude** synthesizes the event log into warm, conversational answers — with photo thumbnails of the relevant frames

```
┌──────────────────────────────────────────────────────────┐
│  Camera  →  Filter Pipeline  →  Gemini Vision  →  SQLite │
│                                                          │
│  Chat App  ←  Claude Synthesis  ←  Gabriel API Server    │
└──────────────────────────────────────────────────────────┘
```

## Features

- **Natural-language queries**: Ask anything — "Is Lee okay?", "When did Lee last use the litter box?", "How active has Lee been today?"
- **Frame thumbnails**: Chat responses include tappable photos of the moments Gabriel is referencing
- **Live view**: Tap "See Lee now" for an instant camera snapshot
- **Smart filtering**: Three-tier pipeline (motion → dedup → heartbeat) keeps API costs under ₹1,200/month
- **Burst mode**: Detects sudden events (falls, collisions) and captures at 4x frame rate for better coverage
- **Proactive alerts**: Telegram notifications for concern events (falls, injuries) and inactivity (cat not seen for 4+ hours)
- **Session aggregation**: "Lee groomed for 18 minutes" not "Lee groomed 7 times"
- **Privacy-first**: Raw footage never leaves your machine. Only individual frames are sent to the vision API. Only structured text events are stored.

## Quick Start

### Prerequisites

- Python 3.11+
- A webcam (built-in or USB) for Phase 1, or an RTSP-capable IP camera
- [Gemini API key](https://ai.google.dev/) (for vision analysis)
- [Anthropic API key](https://console.anthropic.com/) (for chat synthesis)

### Setup

```bash
git clone https://github.com/reubenjake1805/gabriel.git
cd gabriel

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and add your API keys
```

### Run

```bash
python main.py
```

Point your webcam at your cat's favorite spot and watch the logs. You should see output like:

```
19:42:01  INFO  gabriel  Gabriel is running. Press Ctrl+C to stop.
19:42:01  INFO  gabriel  Cameras: ['webcam']
19:42:05  INFO  gabriel  [webcam] Frame accepted: type=motion  motion=4.2%  total=1
19:42:47  INFO  gabriel  [webcam] Frame accepted: type=motion  motion=3.1%  total=2
19:57:01  INFO  gabriel  [webcam] Frame accepted: type=heartbeat  motion=0.3%  total=3
```

## Project Structure

```
gabriel/
├── config.py              # All configuration in one place
├── main.py                # Entry point
├── capture/
│   ├── camera.py          # Camera capture + ring buffer
│   └── filters.py         # Motion detection, dedup, heartbeat, burst mode
├── analysis/
│   └── vision.py          # Gemini vision analyzer (coming soon)
├── storage/
│   ├── database.py        # SQLite event log (coming soon)
│   └── frames.py          # Frame file management (coming soon)
├── api/
│   ├── server.py          # FastAPI app (coming soon)
│   ├── chat.py            # Claude chat endpoint (coming soon)
│   ├── sessions.py        # Session aggregation (coming soon)
│   └── events.py          # Events query endpoint (coming soon)
├── alerts/
│   └── dispatcher.py      # Telegram alerts (coming soon)
├── audio/                 # Phase 2: audio monitoring pipeline
├── requirements.txt
└── .env.example
```

## Configuration

All settings live in `config.py`. Key parameters:

| Parameter | Default | Description |
|---|---|---|
| `CAPTURE_INTERVAL` | 1.0s | Time between frame grabs |
| `MOTION_THRESHOLD` | 2.0% | Pixel change % to trigger motion |
| `DEDUP_THRESHOLD` | 12 | pHash Hamming distance threshold |
| `HEARTBEAT_INTERVAL` | 900s | Forced sample every 15 minutes |
| `BURST_MOTION_THRESHOLD` | 15.0% | Pixel change % to trigger burst mode |
| `BURST_FPS` | 4 | Capture rate during burst mode |
| `SESSION_GAP` | 10 min | Max gap between events in a session |

## Cost

Running with one camera, ~10 hours/day:

| Item | Monthly Cost |
|---|---|
| Gemini Flash (~150 frames/day) | ₹250–750 |
| Claude Sonnet (~10 queries/day) | ₹150–400 |
| Infrastructure | ₹0 (runs locally) |
| **Total** | **~₹400–1,150** |

## Roadmap

- [x] Camera capture module + ring buffer
- [x] Frame filter pipeline (motion, dedup, heartbeat, burst)
- [ ] Gemini vision analyzer
- [ ] SQLite event log
- [ ] API server + Claude chat + session aggregation
- [ ] Alert dispatcher (Telegram)
- [ ] Cloudflare Tunnel
- [ ] Mobile chat app (React Native)
- [ ] RTSP camera support
- [ ] Audio monitoring pipeline (Phase 2)

## Architecture

See [gabriel-architecture.md](gabriel-architecture.md) for the full system design document.

## Built With

- [OpenCV](https://opencv.org/) — frame capture and motion detection
- [ImageHash](https://github.com/JohannesBuchner/imagehash) — perceptual deduplication
- [Gemini Flash](https://ai.google.dev/) — vision analysis
- [Claude](https://www.anthropic.com/) — conversational chat synthesis
- [FastAPI](https://fastapi.tiangolo.com/) — API server
- [SQLite](https://sqlite.org/) — event storage

## License

TBD

---

*Gabriel is named after the guardian angel — because every cat deserves someone watching over them.* 🐱
