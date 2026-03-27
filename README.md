# 🧠 Adaptive Memory System (Ebbinghaus Forgetting Curve)

> HackMarch 2.0 Problem Statement (Certisured): Build an adaptive spaced-repetition system with automation.

This project helps users remember what they learn by scheduling revision reminders at the right time, based on the Ebbinghaus Forgetting Curve.

## ✨ What This Project Demonstrates

- ✅ Spaced repetition with adaptive feedback
- ✅ Demo-time compression (days → minutes)
- ✅ Multiple lessons tracked independently in parallel
- ✅ n8n automation pipeline (Trigger → Logic → Notification)
- ✅ Telegram reminders with inline buttons (Remembered / Forgot)
- ✅ Live Streamlit dashboard for lessons, stats, and upcoming reviews

## 🏗️ Architecture

```text
Streamlit Frontend (8501)
   │
   ├── POST /webhook/new-lesson (preferred path through n8n)
   │
   ▼
n8n (5678) ──▶ FastAPI Backend (8000) ──▶ SQLite (/data/memory.db)
   │                                │
   │                                └── reminder scheduling + status tracking
   │
   ├── Every minute: poll due reminders
   ├── Send Telegram reminder with inline buttons
   └── Receive Telegram callback webhook and post feedback back to backend
```

## 🧪 Demo Intervals (Compressed)

| Review | Real Spacing | Demo Spacing |
|---|---:|---:|
| #1 | 1 day | 1 minute |
| #2 | 3 days | 3 minutes |
| #3 | 7 days | 7 minutes |
| #4 | 14 days | 14 minutes |
| #5 | 30 days | 30 minutes |

## 📦 Tech Stack

- **Frontend:** Streamlit + Plotly
- **Backend:** FastAPI + SQLite
- **Automation:** n8n
- **Notification Channel:** Telegram Bot API
- **Containerization:** Docker Compose

## 📁 Project Structure

```text
backend/
  app.py
  Dockerfile
  requirements.txt
frontend/
  streamlit_app.py
  index.html
  Dockerfile
  requirements.txt
n8n-workflows/
  reminder-checker-workflow.json
data/
  memory.db (runtime)
docker-compose.yml
.env.example
README.md
```

## 🔐 Prerequisites

- Docker Desktop (running)
- Telegram account
- ngrok account (free) for Telegram callback buttons

## 🚀 Full Setup (From Zero)

### 1) Clone the repository

```bash
git clone https://github.com/sarikashirolkar/Adaptive-Memory-System-HackMarch2.0_Hackathon.git
cd Adaptive-Memory-System-HackMarch2.0_Hackathon
```

### 2) Create Telegram bot and get credentials

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Copy the generated bot token
4. Message your new bot once (for example: `hi`)
5. Get your chat ID:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
```

Find: `"chat": { "id": 123456789 }`

### 3) Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and set:

- `TELEGRAM_BOT_TOKEN=<your_bot_token>`
- `TELEGRAM_CHAT_ID=<your_chat_id>`

### 4) Start all services

```bash
docker-compose up --build
```

Open:

- Frontend: http://localhost:8501
- Backend: http://localhost:8000/health
- n8n: http://localhost:5678

### 5) Import n8n workflow

1. Open `http://localhost:5678`
2. Create local n8n account if prompted
3. Create a new workflow
4. Top-right menu (`...`) → **Import from file**
5. Select: `n8n-workflows/reminder-checker-workflow.json`
6. Open each Telegram node and attach a Telegram credential using your bot token
7. Click **Publish** (or **Activate**, depending on n8n version)

### 6) Enable Telegram callback buttons (ngrok)

Start tunnel:

```bash
ngrok http 5678
```

Copy forwarding URL (example: `https://abc123.ngrok-free.app`) and register webhook:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://abc123.ngrok-free.app/webhook/telegram-callback"
```

Verify webhook:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

Keep ngrok running during demo.

## 🎬 Judge-Friendly Demo Script

1. Open Streamlit dashboard (`http://localhost:8501`)
2. Add lesson: `Neural Networks`
3. Show instantly scheduled 5 reviews
4. Wait ~1 minute for Telegram reminder
5. Tap `✅ Remembered` or `❌ Forgot`
6. Show dashboard update in real time
7. Add 2nd and 3rd lesson to prove parallel independent schedules

## 🔁 n8n Workflow Overview

### Flow A: Reminder dispatcher

1. Every Minute Trigger
2. GET due reminders from backend
3. Send Telegram reminder with inline buttons
4. Mark reminder as sent

### Flow B: Telegram callback handling

1. Telegram webhook receives callback
2. Parse callback data (`remembered:<id>` / `forgot:<id>`)
3. POST feedback to backend
4. Answer callback query
5. Edit Telegram message to remove buttons and show status

### Flow C: Frontend lesson intake

1. Frontend posts new lesson to `n8n /webhook/new-lesson`
2. n8n forwards to backend `/api/lessons`
3. n8n optionally sends “new lesson logged” Telegram message
4. n8n responds to frontend with created lesson + reminders

## 🧮 Memory Model

The decay model is:

`R(t) = e^(-t/S)`

- `R`: retention
- `t`: elapsed time since last review
- `S`: memory stability

In this implementation, successful recalls increase effective stability over repetitions, while `forgot` creates a retry reminder for the same review level.

## 🔌 API Reference

- `GET /health`
- `POST /api/lessons`
- `GET /api/lessons`
- `GET /api/lessons/{id}`
- `DELETE /api/lessons/{id}`
- `GET /api/reminders/due`
- `POST /api/reminders/{id}/mark-sent`
- `POST /api/reminders/{id}/feedback`
- `GET /api/reminders/upcoming`
- `GET /api/stats`

## 🛠️ Quick Troubleshooting

### n8n not opening on localhost:5678

```bash
docker-compose down
rm -rf n8n-data
docker-compose up --build
```

### Docker credential helper errors

Ensure Docker Desktop is fully running. If needed, remove invalid helper config from `~/.docker/config.json`.

### Telegram messages not received

- Confirm bot token and chat ID in `.env`
- Confirm workflow is published
- Confirm Telegram credential attached to all Telegram nodes
- Confirm reminder is due (demo mode first reminder is ~1 minute)

### Telegram button click not working

- ngrok must be running
- webhook URL must use exact current ngrok URL
- verify with `getWebhookInfo`

## 🔒 Security Notes

- Never commit `.env`
- Rotate bot token if exposed
- Use a demo-only Telegram bot for hackathon presentations

## 📜 License

MIT (or your preferred license).

---

Built for HackMarch 2.0 with FastAPI + n8n + Streamlit + Telegram 🚀
