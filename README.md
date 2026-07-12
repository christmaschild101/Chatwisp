# Chatwisp

A real-time chat application with forums, direct messages, and admin management. WebSocket-based with Python server and both web and Windows clients.

## Quick Start

### Run your own server

```bash
pip install -r requirements.txt
python server.py --host 0.0.0.0 --port 8765
```

Requires a PostgreSQL database. On first run the server creates all tables and seeds them from `server_data/*.json`. Database credentials come from `DATABASE_URL`:

```bash
export DATABASE_URL="postgresql://user:password@host:port/database"
```

### First admin account

Send this message over WebSocket after connecting:

```json
{"type": "create_dev_account", "username": "admin", "password": "your_password"}
```

Only the first such message succeeds (no prior admin exists).

### Connect a client

- **Web client**: open `http://your-server:8765/` in any browser (server serves `client_web/`).
- **Windows client**: `pip install wxPython && python client_windows.py`.

Both clients default to `wss://chatwisp.onrender.com` — override the URL on the login screen to point to your server.

### Connect to the central server

Navigate to `https://chatwisp.onrender.com/` in a browser, or launch the Windows client and keep the default address. The central server runs the latest stable release.

## Server configuration

All configuration comes from environment variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGPORT`, `PGDATABASE` | Alternative to DATABASE_URL |
| `PORT` | Port for the WebSocket server (Render sets this automatically) |

## Features

- **Forums** — Create, browse, and post in topic-based forums
- **Direct messages** — Real-time one-on-one chat
- **Admin panel** — Manage users, forums, topics; delete posts; ban users
- **Signatures** — Per-user text signatures appended to posts
- **Auto-reconnect** — Clients retry on disconnect with exponential backoff
- **Keepalive pings** — Every 30 seconds to prevent proxy timeouts
- **Rate limiting** — Max 10 login/register attempts per IP per 60 seconds
- **bcrypt passwords** — SHA-256 legacy hashes auto-upgraded on login
- **Security** — Minimum 8-char passwords, super_admin ban protection

## Music

The server serves 5 royalty-free MP3s from the `/music/` endpoint. Music was removed from both clients in v3.3.0. The MP3 files remain in `client_web/music/` (gitignored) for server-side use only.

## Branch workflow

- **`main`** — Working branch deployed to the central server at Render.
- **`source`** — Mirrors `main` after stable releases for public consumption.

## Development

```bash
git clone https://github.com/christmas-child/Chatwisp.git
cd Chatwisp
pip install -r requirements.txt
python server.py
```

No tests, no build step, no CI. Pure Python + static HTML/JS.

## License

MIT
