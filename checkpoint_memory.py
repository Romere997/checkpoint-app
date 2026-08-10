"""
checkpoint_memory.py — Durable, tamper-evident memory for Checkpoint.

Design goals:
- Append-only logical log over the existing SQLite checkpoints table.
- Each entry chains to the previous entry via SHA-256(preimage + prev_hash).
- Periodic signed manifests anchor the chain state.
- Verification endpoint / API lets any agent prove the chain is intact.
- Export produces a verifiable manifest file.

Security model:
- Secrets never embedded in exported manifests by default.
- Chain verification detects inserts, deletes, or modifications.
- Re-verification is cheap: recompute chain from DB rows in order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config / secrets
# ---------------------------------------------------------------------------

MEMORY_DIR = Path(os.environ.get("CHECKPOINT_MEMORY_DIR", "memory"))
MEMORY_DIR.mkdir(exist_ok=True)

CHAIN_FILE = MEMORY_DIR / "chain.jsonl"
MANIFEST_FILE = MEMORY_DIR / "manifest.json"

# Derive a signing secret from env; never hardcode.
SIGNING_SECRET = os.environ.get("CHECKPOINT_MEMORY_SECRET", "").encode("utf-8")
if not SIGNING_SECRET:
    # Fallback only for local dev; rotate in production.
    SIGNING_SECRET = b"checkpoint-memory-dev-secret"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(payload: bytes) -> str:
    return hmac.new(SIGNING_SECRET, payload, hashlib.sha256).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow_ts() -> int:
    return int(time.time())


def _normalize(value: Any) -> str:
    """Canonical JSON for hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# DB helper (reuse existing db() pattern from main.py)
# ---------------------------------------------------------------------------

@contextmanager
def _db():
    import sqlite3
    DB_PATH = os.environ.get("CHECKPOINT_DB", "checkpoints.db")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Chain operations
# ---------------------------------------------------------------------------

def _load_tail() -> tuple[str | None, int]:
    """
    Return (last_hash, next_index) from the chain file.
    If the file is empty, return (None, 0).
    """
    if not CHAIN_FILE.exists() or CHAIN_FILE.stat().st_size == 0:
        return None, 0
    last_hash = None
    last_index = -1
    with CHAIN_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            last_hash = obj["hash"]
            last_index = obj["index"]
    return last_hash, last_index + 1


def append_checkpoint(row: dict[str, Any]) -> dict[str, Any]:
    """
    Append a checkpoint row to the chain.
    Call this AFTER the row is committed to SQLite.
    Returns the chain entry dict.
    """
    prev_hash, index = _load_tail()
    preimage = _normalize({
        "index": index,
        "prev": prev_hash,
        "id": row["id"],
        "agent": row["agent"],
        "kind": row["kind"],
        "question": row["question"],
        "answer": row.get("answer"),
        "answer_reason": row.get("answer_reason"),
        "status": row["status"],
        "created_at": row["created_at"],
        "answered_at": row.get("answered_at"),
        "ts": _utcnow_ts(),
    })
    entry_hash = _sha256(preimage.encode("utf-8"))
    signature = _sign(preimage.encode("utf-8"))

    entry = {
        "index": index,
        "prev": prev_hash,
        "hash": entry_hash,
        "signature": signature,
        "preimage": preimage,
        "ts": _utcnow_iso(),
        "record": {
            "id": row["id"],
            "agent": row["agent"],
            "kind": row["kind"],
            "question": row["question"],
            "answer": row.get("answer"),
            "answer_reason": row.get("answer_reason"),
            "status": row["status"],
            "created_at": row["created_at"],
            "answered_at": row.get("answered_at"),
        },
    }

    with CHAIN_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")

    return entry


def verify_chain() -> dict[str, Any]:
    """
    Recompute the entire chain from the chain file.
    Returns {valid: bool, entries: int, first_ts, last_ts, errors: [...]}.
    """
    errors: list[str] = []
    entries: list[dict[str, Any]] = []
    prev_hash = None
    expected_index = 0

    if not CHAIN_FILE.exists() or CHAIN_FILE.stat().st_size == 0:
        return {"valid": True, "entries": 0, "first_ts": None, "last_ts": None, "errors": []}

    with CHAIN_FILE.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {lineno}: invalid JSON: {exc}")
                continue

            idx = obj.get("index")
            if idx != expected_index:
                errors.append(f"line {lineno}: expected index {expected_index}, got {idx}")
            expected_index = idx + 1

            prev = obj.get("prev")
            if prev != prev_hash:
                errors.append(f"line {lineno}: prev hash mismatch (expected {prev_hash}, got {prev})")

            preimage = obj.get("preimage", "")
            if not preimage:
                errors.append(f"line {lineno}: missing preimage")
            computed = _sha256(preimage.encode("utf-8"))
            if computed != obj.get("hash"):
                errors.append(f"line {lineno}: hash mismatch")

            prev_hash = obj.get("hash")
            entries.append(obj)

    first_ts = entries[0]["ts"] if entries else None
    last_ts = entries[-1]["ts"] if entries else None

    return {
        "valid": len(errors) == 0,
        "entries": len(entries),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "errors": errors,
    }


def export_manifest() -> dict[str, Any]:
    """
    Produce a signed manifest summarizing the current chain state.
    """
    chain_state = verify_chain()
    entries: list[dict[str, Any]] = []

    if CHAIN_FILE.exists():
        with CHAIN_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

    manifest = {
        "type": "checkpoint-memory-manifest",
        "version": 1,
        "generated_at": _utcnow_iso(),
        "chain": chain_state,
        "entry_count": len(entries),
        "last_hash": entries[-1]["hash"] if entries else None,
        "entries": entries,
    }

    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["signature"] = _sign(manifest_bytes)

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# FastAPI router (mount under /memory in main.py)
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/memory", tags=["memory"])


class VerifyResponse(BaseModel):
    valid: bool
    entries: int
    first_ts: str | None
    last_ts: str | None
    errors: list[str]


class ManifestResponse(BaseModel):
    type: str
    version: int
    generated_at: str
    chain: dict[str, Any]
    entry_count: int
    last_hash: str | None
    signature: str


@router.get("/verify", response_model=VerifyResponse)
def api_verify():
    state = verify_chain()
    return VerifyResponse(**state)


@router.get("/manifest", response_model=ManifestResponse)
def api_manifest():
    manifest = export_manifest()
    return ManifestResponse(**manifest)


@router.post("/rebuild")
def api_rebuild():
    """
    Rebuild the chain from the SQLite checkpoints table.
    Use this if the chain file is corrupted or out of sync.
    """
    if CHAIN_FILE.exists():
        CHAIN_FILE.unlink()

    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM checkpoints ORDER BY created_at ASC"
        ).fetchall()

    rebuilt = 0
    for row in rows:
        row_dict = dict(row)
        append_checkpoint(row_dict)
        rebuilt += 1

    state = verify_chain()
    return {"rebuilt": rebuilt, "valid": state["valid"], "errors": state["errors"]}


# ---------------------------------------------------------------------------
# Convenience CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python checkpoint_memory.py verify|manifest|rebuild")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "verify":
        state = verify_chain()
        print(json.dumps(state, indent=2))
        sys.exit(0 if state["valid"] else 1)
    elif cmd == "manifest":
        manifest = export_manifest()
        print(json.dumps(manifest, indent=2))
        sys.exit(0)
    elif cmd == "rebuild":
        result = api_rebuild()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["valid"] else 1)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
