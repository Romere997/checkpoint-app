"""
checkpoint_client.py — drop this into any Hermes agent.

One function: ask_human(). The agent blocks (or polls in the background)
until you answer on your phone, then gets the answer + your reason back.

Setup:
    export CHECKPOINT_URL="https://your-app.onrender.com"   # or http://localhost:8000
    export CHECKPOINT_KEY="your-secret-key"

Usage from an agent:

    from checkpoint_client import ask_human

    result = ask_human(
        agent="Atlas",
        question="Did you make it to the gym?",
        context="Today · 5:30 PM",
    )
    # result -> {"answer": "no", "reason": "work ran late...", ...}

    # Approval-style question with custom options:
    result = ask_human(
        agent="Mercury",
        question="3 crons failed overnight. Restart on the fallback model?",
        kind="approve",
        options=["Restart on fallback", "Hold for review"],
        context="Fleet · flagged 4:12 AM",
    )
    # result["answer"] -> "Restart on fallback"

If the agent shouldn't block, use ask_human_async() to fire the question
and check_answer(checkpoint_id) later from a cron or loop.
"""

import os
import time

import requests

BASE = os.environ.get("CHECKPOINT_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ.get("CHECKPOINT_KEY", "change-me")
HEADERS = {"X-API-Key": KEY}


def ask_human_async(agent, question, kind="verify", options=None, context="",
                    reason_prompt="What got in the way?", priority="normal"):
    """Post the question and return its checkpoint id immediately (non-blocking)."""
    r = requests.post(
        f"{BASE}/api/checkpoints",
        json={
            "agent": agent,
            "question": question,
            "kind": kind,
            "options": options or [],
            "context": context,
            "reason_prompt": reason_prompt,
            "priority": priority,
        },
        headers=HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["id"]


def check_answer(checkpoint_id):
    """Return the checkpoint dict; status is 'pending' or 'answered'."""
    r = requests.get(f"{BASE}/api/checkpoints/{checkpoint_id}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def ask_human(agent, question, kind="verify", options=None, context="",
              reason_prompt="What got in the way?", priority="normal",
              timeout_seconds=3600, poll_seconds=10):
    """Ask and wait for the human's answer. Returns the checkpoint dict, or None on timeout."""
    cid = ask_human_async(agent, question, kind, options, context, reason_prompt, priority)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        cp = check_answer(cid)
        if cp["status"] == "answered":
            return cp
        time.sleep(poll_seconds)
    return None


if __name__ == "__main__":
    # Quick smoke test: sends a question, waits for you to answer on your phone.
    print(f"Posting a test question to {BASE} ...")
    result = ask_human(
        agent="Atlas",
        question="Checkpoint is live. Can you see this on your phone?",
        context="Setup test",
        timeout_seconds=600,
    )
    print("Answer received:", result)
