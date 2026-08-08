# Agent Handoff

## Current state
Checkpoint v0.1.0 is functional. The full loop works: agents ask, phone
answers, agents receive structured results. Server runs on FastAPI + SQLite.
Phone UI is a single-file PWA with live polling.

## What works
- All 6 REST endpoints (create, list, get, answer, stats)
- Yes/No verify questions with one-tap answer
- Approve-style questions with custom options
- Reason capture on "No" — agent receives the human's explanation
- Pending queue badge on the Checkpoint tab
- History tab with follow-through ring (Yes / No / Decisions)
- Per-agent color chips (round-robin, 6-color palette)
- PWA installable to iPhone home screen
- 12-second live polling + refresh on tab switch
- 404 fallback to index.html (SPA mode)
- API-key auth on all endpoints

## What does not work
- No question expiry / TTL (questions stay pending forever)
- No Web Push / real-time delivery (polling only)
- No rate limiting
- No per-user / per-agent key scoping
- No automated test suite
- History resets on redeploy (Render free tier wipes disk)
- No Webhook delivery on answer (agents must poll)

## How to run

```bash
cd C:\Users\rpresendieu\Desktop\checkpoint-app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:CHECKPOINT_KEY="pick-a-secret-key"
python main.py
# Open http://localhost:8000
```

From a second terminal (smoke test):
```bash
export CHECKPOINT_URL=http://localhost:8000
export CHECKPOINT_KEY=pick-a-secret-key
python checkpoint_client.py
```

## Architecture constraints
- SQLite is local to one server instance. For multi-instance: set
  `CHECKPOINT_DB` to a shared persistent volume or migrate to Postgres.
- The gateway never reasons — it only stores questions and records answers.
  All agent logic lives on the agent side.
- All state is in the SQLite DB. No Redis, no session store, no cache.

## Files that must not be casually changed
- `main.py` — schema changes require DB migration or data loss
- `static/index.html` — entire PWA in one file; edits should be incremental
- `.gitignore` — must keep `checkpoints.db` ignored

## Known defects
- `checkpoints.db` can grow unbounded (no expiry, no cleanup)
- Polling every 12 seconds is a battery consideration on the phone
- No CSRF protection (relies on API key in header; not relevant for native
  PWA, but would matter if embedded in a browser context)

## Next prioritized tasks
1. Question TTL field + auto-expire cron
2. Automated test suite (pytest for API, Playwright for PWA)
3. Webhook delivery on answer (eliminate polling for agents)
4. Rate limiting (slowapi)
5. Per-agent stats in History tab

## Required tests
- API: create/list/get/answer/stats endpoints (manual tests pass; pytest TBD)
- Frontend: PWA setup, question render, answer submit, reason flow, history
  tab (Playwright TBD)
- E2E: agent SDK → server → phone PWA → answer → agent receives result
- Security: bad API key → 401, no data leak on auth failure

## Definition of done
- All tests pass
- `checkpoints.db` is excluded from repo
- README reflects current state
- CHANGELOG updated
- Screenshots in `docs/images/` are current
- Deploy to Render and verify end-to-end on real phone

## Human approval required
- Any change to the API contract (new endpoints, field renames)
- Adding external dependencies to `static/index.html`
- Switching database backend
- Any public-facing description or deployment instructions

## Security restrictions
- Never commit API keys or `.env` files
- Never include real checkpoint data in screenshots (strip before capturing)
- Render free tier: data is wiped on redeploy; do not store anything critical
  without a persistent disk

## Rollback instructions
```bash
git revert HEAD
git push origin master
# Render auto-deploys the previous commit
```

## Evidence required before claiming completion
- Server starts cleanly (verified 2026-08-08)
- All 6 endpoints return correct responses (verified)
- Phone PWA renders and answers questions (3 screenshots captured)
- `checkpoints.db` is gitignored (verified)
- Repo is private on GitHub (Romere997/checkpoint-app, verified)
- README, CHANGELOG, DECISIONS, CONTRIBUTING all present (verified)
