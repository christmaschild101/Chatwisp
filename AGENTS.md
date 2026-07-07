# Chatwisp — Agent Guide

## Project structure

```
server.py              # WebSocket server (Python, websockets lib + asyncpg)
client_windows.py      # Windows desktop client (wxPython)
client_web/            # Static HTML+CSS+JS web client (no build step)
server_data/           # JSON seed files (read once on first database init)
```

## Running

```bash
pip install -r requirements.txt   # websockets>=11.0, asyncpg>=0.28.0
python server.py                   # default ws://0.0.0.0:8765 (or $PORT on Render)
python server.py --host 0.0.0.0 --port 8765
```

Requires a PostgreSQL database. On first run the server reads `server_data/*.json` to seed the database, then uses PostgreSQL for all subsequent operations.

## Database

Connection comes from the `DATABASE_URL` env var (or `PGDATABASE_URL`). Example:
```
postgresql://user:password@host:port/database
```

On Render, set `DATABASE_URL` in the Render dashboard Environment Variables section.

On first run, the server creates these tables automatically:

- **`users`** — username, password_hash (SHA-256), is_admin, super_admin (only `christmas_child` has this), banned, ban_reason, ban_duration, created_at
- **`forums`** — id, name, description, created_at
- **`topics`** — id (UUID), forum_id (FK→forums), title, author (FK→users), closed, admin_only, created_at
- **`posts`** — id (UUID), topic_id (FK→topics), author (FK→users), content, created_at
- **`dms`** — id (UUID), sender (FK→users), recipient (FK→users), content, read, created_at
- **`settings`** — key (text PK), value (text) — stores server config like MOTD

To reset the database: drop the tables and restart the server — it will re-seed from `server_data/*.json`.

## Running locally without a database

Not supported. The server requires PostgreSQL (local or remote Supabase instance).

## Clients

Both hardcode `wss://chatwisp.onrender.com`.

- **Web client**: navigate to `https://chatwisp.onrender.com/` in any browser (server serves the static files from `client_web/`). Can also open `client_web/index.html` locally but same-origin fetching works better.
- **Windows client**: `pip install wxPython && python client_windows.py`.

## Building the Windows Installer

Requires Windows with [Inno Setup](https://jrsoftware.org/isdl.php) and Python + PyInstaller.

```bash
# Step 1: Build the executable
pyinstaller --onefile --windowed --name Chatwisp.exe client_windows.py

# Step 2: Copy the exe to the project root
copy dist\Chatwisp.exe .

# Step 3: Open setup.iss in Inno Setup Compiler and click Compile
# Or from command line:
ISCC.exe setup.iss
```

This produces `installer\ChatwispSetup-3.0.0.exe`. The installer:
- Installs Chatwisp.exe to `Program Files\Chatwisp`
- Registers the `chatwisp://` URI protocol (admin rights required — handled by the installer)
- Creates Start Menu and optional desktop shortcut
- Provides an uninstaller

## Admin account

Only the first `create_dev_account` WebSocket message succeeds (no prior admin exists). After that, send `{"type": "create_dev_account", "username": "...", "password": "..."}`.

## Resetting state

Drop the five tables (users, forums, topics, posts, settings) from the database and restart the server. It will re-seed from `server_data/*.json` on the next startup.

## Conventions

- No tests, linting, formatter, typechecker, or CI.
- No build step — pure Python + static files.
- Passwords hashed with SHA-256 (stored in database).
