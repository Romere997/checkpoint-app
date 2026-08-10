# Checkpoint — Human decision gateway for your Hermes fleet

Your agents POST questions. Your phone answers them. Agents get the answer back.

## What's in here

- `main.py` — the FastAPI server (API + serves the phone app)
- `static/` — the phone app (dark UI, installs to your iPhone home screen)
- `checkpoint_client.py` — drop into any Hermes agent, gives it `ask_human()`
- `requirements.txt`

## Step 1 — Run it on your computer (5 minutes)

```bash
pip install -r requirements.txt
export CHECKPOINT_KEY="change-me"
python main.py
```

Open http://localhost:8000 — enter your key. You'll see "All clear."

Send yourself a test question from a second terminal:

```bash
export CHECKPOINT_URL="http://localhost:8000"
export CHECKPOINT_KEY="change-me"
python checkpoint_client.py
```

The question appears in the app within ~12 seconds. Answer it, and the
terminal prints your answer. That's the whole loop.

## Step 2 — Open it on your iPhone (same Wi-Fi)

Find your computer's local IP (Mac: System Settings → Wi-Fi → Details.
Windows: `ipconfig`). Then on your phone open:

```
http://YOUR-COMPUTER-IP:8000
```

Works, but only while your phone is on your home Wi-Fi. For anywhere-access,
do Step 3.

## Step 3 — Deploy it (so it works anywhere, free)

Using Render (easiest):

1. Push this folder to a **private** GitHub repo.
2. Go to render.com → New → Web Service → connect that repo.
3. Settings:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python main.py`
   - Environment variable: `CHECKPOINT_KEY` = your secret key
4. Deploy. You get a URL like `https://checkpoint-xxxx.onrender.com`.

Note: Render's free tier sleeps after inactivity and wipes local disk on
redeploys, so answered history resets when the service restarts. Fine for
proving the loop; when it's part of your daily ops, move it to a $7 instance
with a persistent disk (set `CHECKPOINT_DB=/data/checkpoints.db`) or host it
on your own box behind Tailscale.

## Step 4 — Install on your iPhone

1. Open your Render URL in **Safari**.
2. Enter your key once.
3. Share button → **Add to Home Screen**.

It now opens full-screen with the Checkpoint icon, like a native app.

## Step 5 — Wire in your Hermes agents

Copy `checkpoint_client.py` next to your agent code, set the two env vars,
and any agent can do:

```python
from checkpoint_client import ask_human

result = ask_human(
    agent="Atlas",
    question="Did you make it to the gym?",
    context="Today · 5:30 PM",
)
if result["answer"] == "no":
    reason = result["answer_reason"]
    # Atlas reasons about the reason, then can ask a follow-up:
    followup = ask_human(
        agent="Atlas",
        kind="approve",
        question="Move the workout to tomorrow at 5:30 PM?",
        options=["Move it", "Skip it"],
        context=f'You said: "{reason}"',
    )
```

For agents that shouldn't block, use `ask_human_async()` to fire the
question and `check_answer(id)` from your cron loop — pairs naturally
with cronwrap.

No Python needed? It's plain HTTP:

```bash
curl -X POST https://your-url/api/checkpoints \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"agent":"Nano","question":"Approve the Teams digest send?","kind":"approve","options":["Send","Hold"]}'
```

## The architecture (on purpose)

Agents = the brain. This app = the human authority surface. The server never
reasons — it just moves questions and answers. When you answer "No" with a
reason, the raw reason goes back to the asking agent, and *that agent*
decides what to propose next, usually as a follow-up checkpoint. Keep it
that way: it means all 18 agents share one gateway without the gateway
needing to know anything about any of them.
