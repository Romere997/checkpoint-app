"""
Checkpoint — Human decision gateway for AI agents.

Agents POST questions here. Your phone answers them. Agents poll for the answer.

Run locally:   python main.py          (then open http://YOUR-COMPUTER-IP:8000 on your phone)
Deploy:        works as-is on Render / Railway / Fly (start command: python main.py)

Auth: set the CHECKPOINT_KEY environment variable (defaults to "change-me").
Both your agents and your phone use this same key.
"""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

API_KEY = os.environ.get("CHECKPOINT_KEY", "change-me")
DB_PATH = os.environ.get("CHECKPOINT_DB", "checkpoints.db")
PORT = int(os.environ.get("PORT", 8000))

app = FastAPI(title="Checkpoint", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- database

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'verify',
                question TEXT NOT NULL,
                context TEXT DEFAULT '',
                reason_prompt TEXT DEFAULT 'What got in the way?',
                options TEXT DEFAULT '[]',
                priority TEXT DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'pending',
                answer TEXT,
                answer_reason TEXT,
                created_at REAL NOT NULL,
                answered_at REAL
            )
            """
        )


init_db()


def row_to_dict(r):
    d = dict(r)
    d["options"] = json.loads(d["options"] or "[]")
    return d


def require_key(x_api_key: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Bad or missing X-API-Key")


# ---------------------------------------------------------------- models

class CheckpointIn(BaseModel):
    agent: str = Field(..., examples=["Atlas"])
    question: str
    kind: str = "verify"            # "verify" -> Yes/No, "approve" -> custom options
    context: str = ""               # e.g. "Today · 5:30 PM"
    reason_prompt: str = "What got in the way?"
    options: list[str] = []         # for kind="approve", e.g. ["Restart", "Hold"]
    priority: str = "normal"


class AnswerIn(BaseModel):
    answer: str                     # "yes" / "no" / or an option label
    reason: str = ""                # the human explanation, if any


# ---------------------------------------------------------------- agent API

@app.post("/api/checkpoints")
def create_checkpoint(body: CheckpointIn, x_api_key: str | None = Header(None)):
    """An agent asks the human a question."""
    require_key(x_api_key)
    cid = uuid.uuid4().hex[:12]
    with db() as conn:
        conn.execute(
            "INSERT INTO checkpoints (id, agent, kind, question, context, reason_prompt,"
            " options, priority, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, body.agent, body.kind, body.question, body.context,
             body.reason_prompt, json.dumps(body.options), body.priority, time.time()),
        )
    return {"id": cid, "status": "pending"}


@app.get("/api/checkpoints/{cid}")
def get_checkpoint(cid: str, x_api_key: str | None = Header(None)):
    """An agent polls for the human's answer."""
    require_key(x_api_key)
    with db() as conn:
        r = conn.execute("SELECT * FROM checkpoints WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "No such checkpoint")
    return row_to_dict(r)


@app.get("/api/checkpoints")
def list_checkpoints(status: str = "pending", x_api_key: str | None = Header(None)):
    """The phone app polls this for pending questions. Also handy for agents/debugging."""
    require_key(x_api_key)
    with db() as conn:
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE status=? ORDER BY created_at ASC LIMIT 100",
                (status,),
            ).fetchall()
    return {"checkpoints": [row_to_dict(r) for r in rows]}


@app.post("/api/checkpoints/{cid}/answer")
def answer_checkpoint(cid: str, body: AnswerIn, x_api_key: str | None = Header(None)):
    """The phone app submits the human's decision."""
    require_key(x_api_key)
    with db() as conn:
        r = conn.execute("SELECT * FROM checkpoints WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404, "No such checkpoint")
        if r["status"] == "answered":
            return row_to_dict(r)
        conn.execute(
            "UPDATE checkpoints SET status='answered', answer=?, answer_reason=?, answered_at=? WHERE id=?",
            (body.answer, body.reason, time.time(), cid),
        )
        r = conn.execute("SELECT * FROM checkpoints WHERE id=?", (cid,)).fetchone()
    return row_to_dict(r)


@app.get("/api/stats")
def stats(x_api_key: str | None = Header(None)):
    """Simple follow-through stats for the History screen."""
    require_key(x_api_key)
    with db() as conn:
        rows = conn.execute(
            "SELECT answer, COUNT(*) n FROM checkpoints WHERE status='answered' GROUP BY answer"
        ).fetchall()
    counts = {r["answer"]: r["n"] for r in rows}
    yes = counts.get("yes", 0)
    no = counts.get("no", 0)
    other = sum(n for a, n in counts.items() if a not in ("yes", "no"))
    return {"yes": yes, "no": no, "other": other}


# ---------------------------------------------------------------- phone app

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse("static/manifest.json")


@app.get("/icon.png")
def icon():
    return FileResponse("static/icon.png")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
