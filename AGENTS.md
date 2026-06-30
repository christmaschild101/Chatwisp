# Chatwisp — Agent Guide

## Project structure

```
server.py              # WebSocket server (Python, websockets lib)
client_windows.py      # Windows desktop client (wxPython)
client_web/            # Static HTML+CSS+JS web client (no build step)
server_data/           # JSON persistence: users.json, forums.json, topics.json, posts.json
```

## Running

```bash
pip install -r requirements.txt   # only dep: websockets>=11.0
python server.py                   # default ws://0.0.0.0:8765 (or $PORT on Render)
python server.py --host 0.0.0.0 --port 8765
```

On Render, port comes from `$PORT` env var (default `10000`). Configure health check path to `/healthz` in the Render dashboard. Health check responds at `/healthz`.

Server auto-creates `server_data/` with three default forums on first run.

## Clients

Both hardcode `wss://chatwisp.onrender.com`.

- **Web client**: navigate to `https://chatwisp.onrender.com/` in any browser (server serves the static files from `client_web/`). Can also open `client_web/index.html` locally but same-origin fetching works better.
- **Windows client**: `pip install wxPython && python client_windows.py`.

## Admin account

Only the first `create_dev_account` WebSocket message succeeds (no prior admin exists). After that, send `{"type": "create_dev_account", "username": "...", "password": "..."}`.

## Resetting state

Delete `server_data/*.json` to wipe all data.

## Conventions

- No tests, linting, formatter, typechecker, or CI.
- No build step — pure Python + static files.
- Passwords hashed with SHA-256 (stored in users.json).
