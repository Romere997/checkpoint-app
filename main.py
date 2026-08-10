"""
Checkpoint — Human decision gateway for AI agents.

Agents POST questions here. Your phone answers them. Agents poll for the answer.

Run locally:   python main.py          (then open http://YOUR-COMPUTER-IP:8000 on your phone)
Deploy:        works as-is on Render / Railway / Fly (start command: python main.py)

Auth: set the required CHECKPOINT_KEY environment variable.
Both your agents and your phone use this same key.
"""

import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import shutil
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent


def _env_path(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default)).expanduser()
    return value if value.is_absolute() else BASE_DIR / value


API_KEY = os.environ.get("CHECKPOINT_KEY", "")
if not API_KEY:
    raise RuntimeError("CHECKPOINT_KEY is required")
DB_PATH = str(_env_path("CHECKPOINT_DB", "checkpoints.db"))
PORT = int(os.environ.get("PORT", 8000))
app = FastAPI(title="Checkpoint", docs_url=None, redoc_url=None)


@app.middleware("http")
async def harden_http(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        length = request.headers.get("content-length")
        if length and int(length) > 25 * 1024 * 1024:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    if request.url.path.startswith(("/music/", "/music-cache/", "/music-art/")):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return response


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brain_dumps (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                attachments TEXT NOT NULL DEFAULT '[]',
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                stage INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE brain_dumps ADD COLUMN stage INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brain_dump_events (
                id TEXT PRIMARY KEY,
                brain_dump_id TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'note',
                note TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                rilp_verified INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_brain_dump_events_brain_dump_id ON brain_dump_events(brain_dump_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at ON checkpoints(created_at)")


def _cleanup_older_than(days=100):
    cutoff = time.time() - days * 86400
    with db() as conn:
        conn.execute("DELETE FROM checkpoints WHERE created_at < ?", (cutoff,))
        return conn.total_changes


def _meta_set(key, value):
    with db() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _meta_get(key):
    with db() as conn:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None


init_db()


def row_to_dict(r):
    d = dict(r)
    d["options"] = json.loads(d["options"] or "[]")
    return d


def require_key(x_api_key: str | None):
    if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "Bad or missing X-API-Key")


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


class BrainDumpIn(BaseModel):
    title: str = Field(..., examples=["BuildGrid launch notes"])
    body: str = ""
    attachments: list[str] = []
    tags: list[str] = []
    status: str = "active"
    stage: int = 1


class BrainDumpUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    attachments: list[str] | None = None
    tags: list[str] | None = None
    status: str | None = None
    stage: int | None = None


class BrainDumpEventIn(BaseModel):
    brain_dump_id: str
    event_type: str = "note"
    note: str = ""
    evidence: str = ""
    rilp_verified: bool = False


class BrainDumpEventUpdate(BaseModel):
    event_type: str | None = None
    note: str | None = None
    evidence: str | None = None
    rilp_verified: bool | None = None


# ---------------------------------------------------------------- brain dumps

def _brain_dump_row_to_dict(r):
    d = dict(r)
    d["attachments"] = json.loads(d["attachments"] or "[]")
    d["tags"] = json.loads(d["tags"] or "[]")
    d["stage"] = int(d.get("stage") or 1)
    return d


@app.post("/api/brain-dumps")
def create_brain_dump(body: BrainDumpIn, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    with db() as conn:
        conn.execute(
            "INSERT INTO brain_dumps (id, title, body, attachments, tags, status, stage, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                cid,
                body.title,
                body.body,
                json.dumps(body.attachments),
                json.dumps(body.tags),
                body.status,
                body.stage,
                now,
                now,
            ),
        )
    return {"id": cid, "status": "active"}


@app.get("/api/brain-dumps")
def list_brain_dumps(status: str = "all", x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    with db() as conn:
        if status == "all":
            rows = conn.execute("SELECT * FROM brain_dumps ORDER BY updated_at DESC LIMIT 200").fetchall()
        else:
            rows = conn.execute("SELECT * FROM brain_dumps WHERE status=? ORDER BY updated_at DESC LIMIT 200", (status,)).fetchall()
    return {"brain_dumps": [_brain_dump_row_to_dict(r) for r in rows]}


@app.get("/api/brain-dumps/{bid}")
def get_brain_dump(bid: str, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    with db() as conn:
        r = conn.execute("SELECT * FROM brain_dumps WHERE id=?", (bid,)).fetchone()
    if not r:
        raise HTTPException(404, "No such brain dump")
    return _brain_dump_row_to_dict(r)


@app.patch("/api/brain-dumps/{bid}")
def update_brain_dump(bid: str, body: BrainDumpUpdate, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    with db() as conn:
        r = conn.execute("SELECT * FROM brain_dumps WHERE id=?", (bid,)).fetchone()
        if not r:
            raise HTTPException(404, "No such brain dump")
        fields = {}
        if body.title is not None:
            fields["title"] = body.title
        if body.body is not None:
            fields["body"] = body.body
        if body.attachments is not None:
            fields["attachments"] = json.dumps(body.attachments)
        if body.tags is not None:
            fields["tags"] = json.dumps(body.tags)
        if body.stage is not None:
            fields["stage"] = body.stage
        if body.status is not None:
            fields["status"] = body.status
        if fields:
            fields["updated_at"] = time.time()
            set_clause = ", ".join([f"{k}=?" for k in fields])
            conn.execute(f"UPDATE brain_dumps SET {set_clause} WHERE id=?", (*fields.values(), bid))
        r = conn.execute("SELECT * FROM brain_dumps WHERE id=?", (bid,)).fetchone()
    return _brain_dump_row_to_dict(r)


def _event_row_to_dict(r):
    d = dict(r)
    d["rilp_verified"] = bool(d.get("rilp_verified"))
    return d


@app.delete("/api/brain-dumps/{bid}")
def delete_brain_dump(bid: str, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    with db() as conn:
        r = conn.execute("SELECT id FROM brain_dumps WHERE id=?", (bid,)).fetchone()
        if not r:
            raise HTTPException(404, "No such brain dump")
        conn.execute("DELETE FROM brain_dump_events WHERE brain_dump_id=?", (bid,))
        conn.execute("DELETE FROM brain_dumps WHERE id=?", (bid,))
    return {"ok": True}


@app.post("/api/brain-dumps/{bid}/events")
def create_brain_dump_event(bid: str, body: BrainDumpEventIn, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    eid = uuid.uuid4().hex[:12]
    now = time.time()
    with db() as conn:
        r = conn.execute("SELECT id FROM brain_dumps WHERE id=?", (bid,)).fetchone()
        if not r:
            raise HTTPException(404, "No such brain dump")
        conn.execute(
            """
            INSERT INTO brain_dump_events (id, brain_dump_id, event_type, note, evidence, rilp_verified, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                eid,
                bid,
                body.event_type,
                body.note,
                body.evidence,
                1 if body.rilp_verified else 0,
                now,
            ),
        )
    return {"id": eid}


@app.get("/api/brain-dumps/{bid}/events")
def list_brain_dump_events(bid: str, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM brain_dump_events WHERE brain_dump_id=? ORDER BY created_at ASC",
            (bid,),
        ).fetchall()
    return {"events": [_event_row_to_dict(r) for r in rows]}


@app.patch("/api/brain-dumps/{bid}/events/{eid}")
def update_brain_dump_event(bid: str, eid: str, body: BrainDumpEventUpdate, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    fields = {}
    if body.event_type is not None:
        fields["event_type"] = body.event_type
    if body.note is not None:
        fields["note"] = body.note
    if body.evidence is not None:
        fields["evidence"] = body.evidence
    if body.rilp_verified is not None:
        fields["rilp_verified"] = 1 if body.rilp_verified else 0
    if not fields:
        with db() as conn:
            r = conn.execute("SELECT * FROM brain_dump_events WHERE id=? AND brain_dump_id=?", (eid, bid)).fetchone()
        if not r:
            raise HTTPException(404, "No such event")
        return _event_row_to_dict(r)
    with db() as conn:
        set_clause = ", ".join([f"{k}=?" for k in fields])
        conn.execute(f"UPDATE brain_dump_events SET {set_clause} WHERE id=? AND brain_dump_id=?", (*fields.values(), eid, bid))
        r = conn.execute("SELECT * FROM brain_dump_events WHERE id=? AND brain_dump_id=?", (eid, bid)).fetchone()
    if not r:
        raise HTTPException(404, "No such event")
    return _event_row_to_dict(r)


@app.delete("/api/brain-dumps/{bid}/events/{eid}")
def delete_brain_dump_event(bid: str, eid: str, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    with db() as conn:
        r = conn.execute("SELECT id FROM brain_dump_events WHERE id=? AND brain_dump_id=?", (eid, bid)).fetchone()
        if not r:
            raise HTTPException(404, "No such event")
        conn.execute("DELETE FROM brain_dump_events WHERE id=? AND brain_dump_id=?", (eid, bid))
    return {"deleted": eid}


UPLOAD_DIR = _env_path("CHECKPOINT_UPLOAD_DIR", "uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = _env_path("CHECKPOINT_MUSIC_DIR", "music")
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
TRANSCODED_DIR = _env_path("CHECKPOINT_MUSIC_TRANSCODED_DIR", "music_cache")
TRANSCODED_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_ART_DIR = _env_path("CHECKPOINT_MUSIC_ART_DIR", "music_art")
MUSIC_ART_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_ALLOWED = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".webm": "audio/webm",
}
MUSIC_COMPAT_SUFFIXES = {".flac", ".wav"}
MUSIC_TRANSCODE_BITRATE = os.environ.get("CHECKPOINT_MUSIC_TRANSCODE_BITRATE", "320k")
MUSIC_TRANSCODE_MAX_BYTES = int(os.environ.get("CHECKPOINT_MUSIC_TRANSCODE_MAX_BYTES", str(1024 ** 3)))
_TRANSCODE_LOCK = threading.Lock()


def _music_id(path: Path) -> str:
    stat = path.stat()
    fingerprint = f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
    return hashlib.sha256(fingerprint).hexdigest()[:16]


def _music_files():
    return [
        path for path in sorted(MUSIC_DIR.iterdir(), key=lambda p: p.name.casefold())
        if path.is_file() and path.suffix.lower() in MUSIC_ALLOWED
    ]


def _find_music_track(track_id: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16}", track_id):
        return None
    for path in _music_files():
        if hmac.compare_digest(_music_id(path), track_id):
            return path.resolve()
    return None


@lru_cache(maxsize=256)
def _probe_audio_cached(path: str, size: int, mtime_ns: int) -> dict:
    del size, mtime_ns
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels,bits_per_sample:format=duration,bit_rate:format_tags=title,artist,album",
                "-of", "json", path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [{}])[0]
        fmt = payload.get("format") or {}
        tags = fmt.get("tags") or {}
        return {
            "codec": stream.get("codec_name"),
            "sample_rate": int(stream["sample_rate"]) if stream.get("sample_rate") else None,
            "channels": stream.get("channels"),
            "bits_per_sample": stream.get("bits_per_sample") or None,
            "duration": float(fmt["duration"]) if fmt.get("duration") else None,
            "bit_rate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
            "title": tags.get("title"),
            "artist": tags.get("artist"),
            "album": tags.get("album"),
        }
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def _probe_audio(path: Path) -> dict:
    stat = path.stat()
    return _probe_audio_cached(str(path), stat.st_size, stat.st_mtime_ns)


def _compat_path(path: Path) -> Path:
    return TRANSCODED_DIR / f"{_music_id(path)}.m4a"


def _cover_art(path: Path) -> str | None:
    track_id = _music_id(path)
    dest = MUSIC_ART_DIR / f"{track_id}.jpg"
    missing = MUSIC_ART_DIR / f"{track_id}.none"
    if dest.exists():
        return f"/music-art/{dest.name}"
    if missing.exists():
        return None
    temp = MUSIC_ART_DIR / f".{track_id}.jpg"
    try:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
                "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=900:900:force_original_aspect_ratio=decrease",
                str(temp),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        os.replace(temp, dest)
        return f"/music-art/{dest.name}"
    except (OSError, subprocess.SubprocessError):
        temp.unlink(missing_ok=True)
        missing.touch()
        return None


def _transcode_compat(src: Path, dest: Path):
    if src.stat().st_size > MUSIC_TRANSCODE_MAX_BYTES:
        raise HTTPException(413, "Track is too large to transcode")
    TRANSCODED_DIR.mkdir(parents=True, exist_ok=True)
    with _TRANSCODE_LOCK:
        if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
            return
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=TRANSCODED_DIR, suffix=".m4a", delete=False) as temp:
                temp_path = Path(temp.name)
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
                    "-vn", "-c:a", "aac", "-b:a", MUSIC_TRANSCODE_BITRATE,
                    "-ac", "2", "-movflags", "+faststart", "-map_metadata", "-1",
                    str(temp_path),
                ],
                check=True,
                capture_output=True,
                timeout=600,
            )
            os.replace(temp_path, dest)
        except (OSError, subprocess.SubprocessError) as exc:
            if temp_path:
                temp_path.unlink(missing_ok=True)
            raise HTTPException(500, "Compatibility encode failed") from exc


@app.get("/api/music")
def list_music(x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    items = []
    for path in _music_files():
        suffix = path.suffix.lower()
        track_id = _music_id(path)
        compat = _compat_path(path)
        items.append({
            "id": track_id,
            "name": path.name,
            "url": f"/music/{quote(path.name, safe='')}",
            "size": path.stat().st_size,
            "mime": MUSIC_ALLOWED[suffix],
            "needs_compat": suffix in MUSIC_COMPAT_SUFFIXES,
            "compat_ready": compat.exists(),
            "compat_url": f"/music-cache/{compat.name}" if compat.exists() else None,
            "art_url": _cover_art(path),
            **_probe_audio(path),
        })
    return {"items": items}


@app.post("/api/music/prepare/{track_id}")
def prepare_music(track_id: str, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    src = _find_music_track(track_id)
    if not src:
        raise HTTPException(404, "No such track")
    if src.suffix.lower() not in MUSIC_COMPAT_SUFFIXES:
        return {"url": f"/music/{quote(src.name, safe='')}", "passthrough": True}
    dest = _compat_path(src)
    _transcode_compat(src, dest)
    return {"url": f"/music-cache/{dest.name}", "passthrough": False}


app.mount("/music", StaticFiles(directory=str(MUSIC_DIR)), name="music-uploads")
app.mount("/music-cache", StaticFiles(directory=str(TRANSCODED_DIR)), name="music-cache")
app.mount("/music-art", StaticFiles(directory=str(MUSIC_ART_DIR)), name="music-art")

@app.post("/api/brain-dumps/upload")
def upload_brain_dump_attachment(file: UploadFile = File(...), x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    filename = file.filename or "attachment"
    safe_name = "".join(c for c in filename if c.isalnum() or c in ("-", "_", ".")).strip() or "attachment"
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {"filename": dest.name, "url": f"/uploads/{dest.name}"}


app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="brain-uploads")


# ---------------------------------------------------------------- agent API

@app.post("/api/checkpoints")
def create_checkpoint(body: CheckpointIn, x_api_key: str | None = Header(None)):
    """An agent asks the human a question."""
    require_key(x_api_key)
    cid = uuid.uuid4().hex[:12]
    created_at = time.time()
    with db() as conn:
        conn.execute(
            "INSERT INTO checkpoints (id, agent, kind, question, context, reason_prompt,"
            " options, priority, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, body.agent, body.kind, body.question, body.context,
             body.reason_prompt, json.dumps(body.options), body.priority, created_at),
        )
    try:
        import checkpoint_memory as cm
        cm.append_checkpoint({
            "id": cid,
            "agent": body.agent,
            "kind": body.kind,
            "question": body.question,
            "answer": None,
            "answer_reason": "",
            "status": "pending",
            "created_at": created_at,
            "answered_at": None,
        })
    except Exception:
        pass
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
    try:
        import checkpoint_memory as cm
        cm.append_checkpoint(dict(r))
    except Exception:
        pass
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

try:
    from checkpoint_memory import router as memory_router
    app.include_router(memory_router)
except Exception:
    pass

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return RedirectResponse("/checkpoint", status_code=307)


@app.get("/checkpoint")
def checkpoint_path():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.json")


@app.get("/icon.png")
def icon():
    return FileResponse(STATIC_DIR / "icon.png")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    path = request.url.path
    if path.startswith("/api") or path.startswith("/memory"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
