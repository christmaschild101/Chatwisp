# Chatwisp

A real-time chat application with forums, direct messages, and admin management. WebSocket-based with Python server and both web and Windows clients.

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

Both clients default to `wss://chatwisp.onrender.com` — override the URL on the login screen to point to your server.

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
