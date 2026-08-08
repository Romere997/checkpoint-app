# Contributing

Checkpoint is a personal project by Ross Presendieu. Contributions are welcome.

## Dev setup

```bash
git clone https://github.com/Romere997/checkpoint-app.git
cd checkpoint-app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export CHECKPOINT_KEY=dev-key
python main.py
```

## Frontend dev

`static/index.html` is the entire PWA — no build step, no bundler, no
dependencies. Edit it directly and refresh the browser.

## API contract

| Method | Path | Description |
|---|---|---|
| POST | `/api/checkpoints` | Create a question |
| GET | `/api/checkpoints/{id}` | Get one question + status |
| GET | `/api/checkpoints?status=pending` | List pending (phone polls this) |
| GET | `/api/checkpoints?status=all` | List everything |
| POST | `/api/checkpoints/{id}/answer` | Submit answer |
| GET | `/api/stats` | Follow-through counts |

All endpoints require `X-API-Key: <your-key>`.

## Code style

- Python: follow PEP 8, type hints on public functions
- Frontend: no frameworks, keep it vanilla, no external CDN deps
- Commit messages: short imperative ("add TTL", "fix 404 fallback")
