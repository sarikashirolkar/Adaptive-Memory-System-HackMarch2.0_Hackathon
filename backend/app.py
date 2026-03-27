from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional, Literal
import sqlite3
import os
import math

app = FastAPI(title="Adaptive Memory System - Ebbinghaus Forgetting Curve")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.getenv("DB_PATH", "/data/memory.db")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

# Spaced repetition intervals
# Demo: days compressed to minutes (1 day = 1 minute)
# Real: actual days in minutes
REVIEW_INTERVALS_DEMO = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}   # minutes
REVIEW_INTERVALS_REAL = {
    1: 1440,    # 1 day
    2: 4320,    # 3 days
    3: 10080,   # 7 days
    4: 20160,   # 14 days
    5: 43200,   # 30 days
}

INTERVAL_LABELS = {1: "1 day", 2: "3 days", 3: "7 days", 4: "14 days", 5: "30 days"}


def get_interval_minutes(review_number: int, demo: bool = None) -> int:
    use_demo = demo if demo is not None else DEMO_MODE
    return REVIEW_INTERVALS_DEMO[review_number] if use_demo else REVIEW_INTERVALS_REAL[review_number]


@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS lessons (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                content     TEXT DEFAULT '',
                created_at  TEXT NOT NULL,
                demo_mode   INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id        INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                review_number    INTEGER NOT NULL,
                scheduled_at     TEXT NOT NULL,
                sent_at          TEXT,
                telegram_msg_id  INTEGER,
                status           TEXT NOT NULL DEFAULT 'pending',
                responded_at     TEXT,
                next_reminder_id INTEGER,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_reminders_scheduled ON reminders(scheduled_at);
            CREATE INDEX IF NOT EXISTS idx_reminders_status    ON reminders(status);
            CREATE INDEX IF NOT EXISTS idx_reminders_lesson    ON reminders(lesson_id);
        """)


init_db()


# ---------- Pydantic Models ----------

class LessonCreate(BaseModel):
    title: str
    content: str = ""
    demo_mode: bool = True


class FeedbackRequest(BaseModel):
    response: str  # "remembered" or "forgot"


class MarkSentRequest(BaseModel):
    telegram_message_id: Optional[int] = None


# ---------- Helper ----------

def retention_percent(review_number: int) -> int:
    """
    Ebbinghaus retention model: R = e^(-t/S)
    After each successful review, stability S doubles.
    This gives an approximate retention at the NEXT review point.
    """
    stability = 1.0
    for _ in range(review_number):
        stability *= 2.5
    t = stability  # time elapsed equals the stability (optimal review point)
    retention = math.exp(-t / (stability * 2.5))
    return max(10, min(100, int(retention * 100)))


def row_to_dict(row) -> dict:
    return dict(row) if row else None


# ---------- Routes ----------

@app.get("/health")
def health():
    return {"status": "ok", "demo_mode": DEMO_MODE}


@app.post("/api/lessons", status_code=201)
def create_lesson(body: LessonCreate):
    now = datetime.utcnow()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO lessons (title, content, created_at, demo_mode) VALUES (?, ?, ?, ?)",
            (body.title, body.content, now.isoformat(), int(body.demo_mode)),
        )
        lesson_id = cursor.lastrowid

        reminders = []
        for review_number in range(1, 6):
            delta = get_interval_minutes(review_number, body.demo_mode)
            scheduled_at = now + timedelta(minutes=delta)
            r = conn.execute(
                "INSERT INTO reminders (lesson_id, review_number, scheduled_at, created_at) VALUES (?, ?, ?, ?)",
                (lesson_id, review_number, scheduled_at.isoformat(), now.isoformat()),
            )
            reminders.append({
                "id": r.lastrowid,
                "review_number": review_number,
                "interval_label": INTERVAL_LABELS[review_number],
                "scheduled_at": scheduled_at.isoformat(),
                "status": "pending",
            })

    return {
        "lesson": {
            "id": lesson_id,
            "title": body.title,
            "content": body.content,
            "created_at": now.isoformat(),
            "demo_mode": body.demo_mode,
        },
        "reminders": reminders,
        "message": f"Lesson logged! {len(reminders)} reviews scheduled.",
    }


@app.get("/api/lessons")
def list_lessons():
    with get_db() as conn:
        lessons = conn.execute(
            "SELECT * FROM lessons ORDER BY created_at DESC"
        ).fetchall()

        result = []
        for lesson in lessons:
            reminders = conn.execute(
                "SELECT * FROM reminders WHERE lesson_id=? ORDER BY review_number",
                (lesson["id"],),
            ).fetchall()

            next_pending = next(
                (r for r in reminders if r["status"] == "pending"), None
            )
            completed = sum(1 for r in reminders if r["status"] in ("remembered", "forgot"))

            result.append({
                **row_to_dict(lesson),
                "reminders": [row_to_dict(r) for r in reminders],
                "reminders_total": len(reminders),
                "reminders_completed": completed,
                "next_review_at": next_pending["scheduled_at"] if next_pending else None,
                "next_review_number": next_pending["review_number"] if next_pending else None,
            })
        return result


@app.get("/api/lessons/{lesson_id}")
def get_lesson(lesson_id: int):
    with get_db() as conn:
        lesson = conn.execute(
            "SELECT * FROM lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        if not lesson:
            raise HTTPException(404, "Lesson not found")
        reminders = conn.execute(
            "SELECT * FROM reminders WHERE lesson_id=? ORDER BY review_number",
            (lesson_id,),
        ).fetchall()
        return {**row_to_dict(lesson), "reminders": [row_to_dict(r) for r in reminders]}


@app.delete("/api/lessons/{lesson_id}")
def delete_lesson(lesson_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
    return {"message": "Deleted"}


@app.get("/api/reminders/due")
def get_due_reminders():
    """Polled by n8n every minute. Returns pending reminders whose time has come."""
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.lesson_id, r.review_number, r.scheduled_at,
                   l.title as lesson_title, l.content as lesson_content
            FROM reminders r
            JOIN lessons l ON r.lesson_id = l.id
            WHERE r.status = 'pending'
              AND r.sent_at IS NULL
              AND r.scheduled_at <= ?
            ORDER BY r.scheduled_at ASC
            """,
            (now,),
        ).fetchall()
        return [
            {
                **row_to_dict(r),
                "interval_label": INTERVAL_LABELS[r["review_number"]],
                "retention_estimate": retention_percent(r["review_number"]),
            }
            for r in rows
        ]


@app.post("/api/reminders/{reminder_id}/mark-sent")
def mark_sent(reminder_id: int, body: MarkSentRequest):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        reminder = conn.execute(
            "SELECT * FROM reminders WHERE id=?", (reminder_id,)
        ).fetchone()
        if not reminder:
            raise HTTPException(404, "Reminder not found")
        conn.execute(
            "UPDATE reminders SET status='sent', sent_at=?, telegram_msg_id=? WHERE id=?",
            (now, body.telegram_message_id, reminder_id),
        )
    return {"status": "sent", "reminder_id": reminder_id}


@app.post("/api/reminders/{reminder_id}/feedback")
def record_feedback(reminder_id: int, body: FeedbackRequest):
    if body.response not in ("remembered", "forgot"):
        raise HTTPException(400, "response must be 'remembered' or 'forgot'")

    now = datetime.utcnow()
    with get_db() as conn:
        reminder = conn.execute(
            "SELECT * FROM reminders WHERE id=?", (reminder_id,)
        ).fetchone()
        if not reminder:
            raise HTTPException(404, "Reminder not found")
        if reminder["status"] in ("remembered", "forgot"):
            raise HTTPException(409, "Feedback already recorded")

        if body.response == "remembered":
            conn.execute(
                "UPDATE reminders SET status='remembered', responded_at=? WHERE id=?",
                (now.isoformat(), reminder_id),
            )
            return {"reminder_id": reminder_id, "status": "remembered", "new_reminder": None}

        else:  # forgot — reschedule same review_number
            lesson = conn.execute(
                "SELECT * FROM lessons WHERE id=?", (reminder["lesson_id"],)
            ).fetchone()
            delta = get_interval_minutes(reminder["review_number"], bool(lesson["demo_mode"]))
            new_scheduled = now + timedelta(minutes=delta)
            cursor = conn.execute(
                "INSERT INTO reminders (lesson_id, review_number, scheduled_at, created_at) VALUES (?, ?, ?, ?)",
                (reminder["lesson_id"], reminder["review_number"], new_scheduled.isoformat(), now.isoformat()),
            )
            new_id = cursor.lastrowid
            conn.execute(
                "UPDATE reminders SET status='forgot', responded_at=?, next_reminder_id=? WHERE id=?",
                (now.isoformat(), new_id, reminder_id),
            )
            return {
                "reminder_id": reminder_id,
                "status": "forgot",
                "new_reminder": {
                    "id": new_id,
                    "review_number": reminder["review_number"],
                    "scheduled_at": new_scheduled.isoformat(),
                    "status": "pending",
                },
            }


@app.get("/api/reminders/upcoming")
def upcoming_reminders(limit: int = 20):
    with get_db() as conn:
        now = datetime.utcnow()
        rows = conn.execute(
            """
            SELECT r.id, r.lesson_id, r.review_number, r.scheduled_at, r.status,
                   l.title as lesson_title
            FROM reminders r
            JOIN lessons l ON r.lesson_id = l.id
            WHERE r.status IN ('pending', 'sent')
            ORDER BY r.scheduled_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        result = []
        for r in rows:
            scheduled = datetime.fromisoformat(r["scheduled_at"])
            delta_seconds = (scheduled - now).total_seconds()
            result.append({
                **row_to_dict(r),
                "interval_label": INTERVAL_LABELS.get(r["review_number"], "?"),
                "seconds_until_due": max(0, int(delta_seconds)),
                "is_overdue": delta_seconds < 0,
            })
        return result


@app.get("/api/stats")
def get_stats():
    with get_db() as conn:
        total_lessons = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        total_reminders = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE status IN ('pending','sent')"
        ).fetchone()[0]
        remembered = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE status='remembered'"
        ).fetchone()[0]
        forgot = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE status='forgot'"
        ).fetchone()[0]

        responded = remembered + forgot
        retention_rate = round(remembered / responded, 2) if responded > 0 else 0.0

        next_due = conn.execute(
            """
            SELECT scheduled_at FROM reminders
            WHERE status='pending' AND sent_at IS NULL
            ORDER BY scheduled_at ASC LIMIT 1
            """
        ).fetchone()

        next_in_seconds = None
        if next_due:
            delta = (datetime.fromisoformat(next_due[0]) - datetime.utcnow()).total_seconds()
            next_in_seconds = max(0, int(delta))

        return {
            "total_lessons": total_lessons,
            "total_reminders": total_reminders,
            "reminders_pending": pending,
            "reminders_remembered": remembered,
            "reminders_forgot": forgot,
            "retention_rate": retention_rate,
            "next_review_in_seconds": next_in_seconds,
            "demo_mode": DEMO_MODE,
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
