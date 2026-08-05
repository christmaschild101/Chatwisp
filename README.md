# Chatwisp

A real-time chat application with forums, direct messages, and admin management. WebSocket-based with Python server and web, Windows, and Linux clients.

## Quick Start

### Run your own server

```bash
pip install -r requirements.txt
python server.py
```

That's it. The server uses SQLite by default — no database server or environment variables needed. A `chatwisp.db` file will be created automatically, and the first admin account is created via WebSocket after the server starts.

For a PostgreSQL-powered server (used by the central deployment), set the `DATABASE_URL` environment variable:

```bash
export DATABASE_URL="postgresql://user:password@host:port/database"
python server.py
```

On first run the server creates all tables automatically and populates three starter forums (General Discussion, Technology, Off Topic). The first admin account is created via WebSocket.

### First admin account

Send this message over WebSocket after connecting:

```json
{"type": "create_dev_account", "username": "admin", "password": "your_password"}
```

Only the first such message succeeds (no prior admin exists).

### Connect a client

- **Web client**: open `http://your-server:8765/` in any browser (server serves `client_web/`).
- **Windows client**: `pip install wxPython && python client_windows.py`.
- **Linux client (GUI)**: `pip install wxPython websockets && python3 client_linux.py` — a desktop GUI identical in layout and features to the Windows client.
- **Linux client (terminal)**: `pip install websockets && python3 client_linux_curses.py` — curses TUI for SSH/headless use.

All clients default to `wss://chatwisp.onrender.com` — override the URL on the login screen to point to your server.

### Connect to the central server

Navigate to `https://chatwisp.onrender.com/` in a browser, or launch the Windows client and keep the default address. The central server runs the latest stable release.

## Server configuration

All configuration comes from environment variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (omit for SQLite) |
| `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGPORT`, `PGDATABASE` | Alternative to DATABASE_URL |
| `SQLITE_PATH` | Path for SQLite database file (default: `chatwisp.db`) |
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

## Voice channels

Users can create per-forum voice channels and join them for real-time conversation. Voice uses **WebRTC peer-to-peer** — the server only relays signaling (SDP offer/answer, ICE candidates). No audio data passes through the server.

- **Create** — any user can create a voice channel in a forum (`voice_create`)
- **Join / Leave** — join a channel to see other members; leave to disconnect
- **Mute / Deafen** — mute your mic or deafen all audio; state propagates to other members
- **Signaling** — the server forwards WebRTC handshake messages between peers in the same channel (`voice_signal`)
- **Rate capped** — max 20 signaling messages per second per user to prevent abuse
- **STUN only** — uses Google's public STUN server (`stun:stun.l.google.com:19302`). No TURN server — users behind symmetric NAT will not be able to connect P2P
- **No persistence** — channel membership is in-memory only (lost on server restart/spin-down). Channels themselves persist in the database

Voice is available on the web client. The Windows client does not currently support voice.

## Music

The server serves 5 royalty-free MP3s from the `/music/` endpoint. Music was removed from both clients in v3.3.0. The MP3 files remain in `client_web/music/` (gitignored) for server-side use only.

## Branch workflow

- **`main`** — Working branch deployed to the central server at Render.
- **`source`** — Mirrors `main` after stable releases for public consumption.

## Linux client

Two Linux clients are provided; both speak the same WebSocket protocol as the
Windows client, so Linux, Windows and web users all share the same forums,
topics, posts and direct messages.

### GUI client (`client_linux.py`)

A wxPython desktop GUI, layout- and feature-identical to the Windows client.
Requires a graphical session (not suitable over plain SSH).

```bash
pip install wxPython websockets
python3 client_linux.py
```

### Terminal client (`client_linux_curses.py`)

A curses-based TUI for use in any terminal, including over SSH, and
screen-reader friendly: status announcements are spoken through
`spd-say`/`espeak`/`espeak-ng` when available, mirroring the Windows client's
NVDA/SAPI accessibility support.

```bash
pip install websockets
python3 client_linux_curses.py
```

| Key | Action |
|---|---|
| ↑/↓ or j/k | Move selection |
| Enter | Open / activate |
| Esc / Backspace | Go back |
| r | Reply to a topic · i | Type a message / edit |
| m | Messages · s | Settings · n | New message |
| F1 | Server uptime · F2 | Ping |
| q | Quit (from main menu) |

Both clients let you sign in with a username/password, or choose "Sign in with
christmaschild Account" to use OAuth. The full admin panel (accounts,
ban/unban, promote/demote, reset password, set MOTD, official-account actions)
and topic moderation (close / reopen / delete / admin-only / delete post) are
supported. The GUI also includes a username/password **Register** button
(matching the Windows client), which the TUI does not.

## Development

```bash
git clone https://github.com/christmaschild101/Chatwisp.git
cd Chatwisp
pip install -r requirements.txt
python server.py
```

No tests, no build step, no CI. Pure Python + static HTML/JS.

## License

MIT
