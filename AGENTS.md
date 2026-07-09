# Chatwisp — Agent Guide

## Project structure

```
server.py              # WebSocket server (Python, websockets lib + asyncpg, bcrypt)
client_windows.py      # Windows desktop client (wxPython)
client_web/            # Static HTML+CSS+JS web client (no build step)
server_data/           # JSON seed files (read once on first database init)
site/                  # Download page + Chatwisp.exe for distribution
```

## Running

```bash
pip install -r requirements.txt   # websockets>=11.0, asyncpg>=0.28.0, bcrypt>=4.0.0
python server.py                   # default ws://0.0.0.0:8765 (or $PORT on Render)
python server.py --host 0.0.0.0 --port 8765
```

Requires a PostgreSQL database. On first run the server reads `server_data/*.json` to seed the database, then uses PostgreSQL for all subsequent operations. Database credentials come from environment variables only (PGHOST, PGUSER, PGPASSWORD, PGPORT, PGDATABASE) or DATABASE_URL.

## Database

Connection comes from the `DATABASE_URL` env var (or `PGDATABASE_URL`). Example:
```
postgresql://user:password@host:port/database
```

On Render, set `DATABASE_URL` in the Render dashboard Environment Variables section.

On first run, the server creates these tables automatically:

- **`users`** — username, password_hash (bcrypt; legacy SHA-256 hashes auto-upgraded on login), is_admin, super_admin (only `christmas_child` has this), banned, ban_reason, ban_duration, created_at
- **`forums`** — id, name, description, created_at
- **`topics`** — id (UUID), forum_id (FK→forums), title, author (FK→users), closed, admin_only, created_at
- **`posts`** — id (UUID), topic_id (FK→topics), author (FK→users), content, created_at
- **`dms`** — id (UUID), sender (FK→users), recipient (FK→users), content, read, created_at
- **`settings`** — key (text PK), value (text) — stores server config like MOTD

To reset the database: drop the six tables and restart the server — it will re-seed from `server_data/*.json` on the next startup.

## Clients

Both hardcode `wss://chatwisp.onrender.com`.

- **Web client**: navigate to `https://chatwisp.onrender.com/` in any browser (server serves the static files from `client_web/`). Can also open `client_web/index.html` locally but same-origin fetching works better.
- **Windows client**: `pip install wxPython && python client_windows.py`.

## Building the Windows Executable

```bash
pyinstaller --onefile --windowed --name Chatwisp.exe client_windows.py
cp dist/Chatwisp.exe site/Chatwisp.exe
```

## Admin account

Only the first `create_dev_account` WebSocket message succeeds (no prior admin exists). After that, send `{"type": "create_dev_account", "username": "...", "password": "..."}`.

## Resetting state

Drop the six tables (users, forums, topics, posts, dms, settings) from the database and restart the server. It will re-seed from `server_data/*.json` on the next startup.

## Security notes (v3.0.1)

- Passwords hashed with bcrypt; existing SHA-256 hashes auto-upgraded on next successful login.
- Rate limiting: max 10 login or register attempts per IP per 60 seconds.
- Only super_admins can ban admin users.
- Minimum password length is 8 characters.
- No hardcoded credentials; all DB config comes from environment variables.

## Conventions

- No tests, linting, formatter, typechecker, or CI.
- No build step — pure Python + static files.
- Passwords hashed with bcrypt (stored in database).
