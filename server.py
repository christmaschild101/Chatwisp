#!/usr/bin/env python3
import asyncio
import http
import json
import hashlib
import os
import signal
import uuid
import sys
from datetime import datetime

VERSION = "2.0.0"

try:
    import websockets
    from websockets.http11 import Response
    from websockets.datastructures import Headers
except ImportError:
    print("Error: websockets library not found. Run: pip install websockets")
    sys.exit(1)

try:
    import asyncpg
except ImportError:
    print("Error: asyncpg library not found. Run: pip install asyncpg")
    sys.exit(1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
FORUMS_FILE = os.path.join(DATA_DIR, "forums.json")
TOPICS_FILE = os.path.join(DATA_DIR, "topics.json")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")




DEFAULT_FORUMS = [
    {"id": "general", "name": "General Discussion", "description": "Talk about anything and everything"},
    {"id": "technology", "name": "Technology", "description": "Discuss technology, software, and hardware"},
    {"id": "offtopic", "name": "Off Topic", "description": "Casual conversation and fun stuff"},
]

CLIENT_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_web")

def _read_static(path):
    try:
        with open(os.path.join(CLIENT_WEB_DIR, path), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None

INDEX_HTML = _read_static("index.html")
APP_JS = _read_static("app.js")
STYLE_CSS = _read_static("style.css")

def _read_json(path, default=None):
    if default is None:
        default = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


class Database:
    def __init__(self):
        self.pool = None
        self._connect_kwargs = self._resolve_connect_kwargs()

    @staticmethod
    def _resolve_connect_kwargs():
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("PGDATABASE_URL")
        if dsn:
            dsn = dsn.split("://", 1)[1] if "://" in dsn else dsn
            userinfo, hostport = dsn.rsplit("@", 1)
            user, _, password = userinfo.partition(":")
            from urllib.parse import unquote
            user = unquote(user)
            password = unquote(password)
            host = hostport
            port = 5432
            database = "postgres"
            if "/" in host:
                host, _, database = host.partition("/")
                database = unquote(database)
            if ":" in host:
                host, _, port_str = host.rpartition(":")
                port = int(port_str)
            return {"host": host, "port": port, "user": user, "password": password, "database": database, "min_size": 1, "max_size": 3}
        host = os.environ.get("PGHOST")
        user = os.environ.get("PGUSER")
        password = os.environ.get("PGPASSWORD")
        if host and user and password:
            port = int(os.environ.get("PGPORT", 5432))
            database = os.environ.get("PGDATABASE", "postgres")
            return {"host": host, "port": port, "user": user, "password": password, "database": database, "min_size": 1, "max_size": 3}
        return {
            "host": "aws-1-ca-central-1.pooler.supabase.com",
            "port": 5432,
            "user": "postgres.biwzptrgxgbojhgquwvo",
            "password": "harpertheblindgamer2026@ca",
            "database": "postgres",
            "min_size": 1,
            "max_size": 3,
        }

    async def connect(self):
        safe = {**self._connect_kwargs, "password": "***"}
        print(f"Connecting to database: host={safe.get('host')} port={safe.get('port')} user={safe.get('user')} database={safe.get('database')}", flush=True)
        for attempt in range(5):
            try:
                self.pool = await asyncpg.create_pool(**self._connect_kwargs)
                break
            except asyncpg.InvalidPasswordError:
                raise
            except Exception as e:
                print(f"DB connection attempt {attempt+1} failed: {e}", flush=True)
                if attempt < 4:
                    await asyncio.sleep(3)
        else:
            raise RuntimeError("Could not connect to database after 5 attempts")
        await self._init_schema()
        await self._seed_from_json()

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _init_schema(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    super_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    banned BOOLEAN NOT NULL DEFAULT FALSE,
                    ban_reason TEXT,
                    ban_duration TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS forums (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS topics (
                    id UUID PRIMARY KEY,
                    forum_id TEXT NOT NULL REFERENCES forums(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    closed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id UUID PRIMARY KEY,
                    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                    author TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS dms (
                    id UUID PRIMARY KEY,
                    sender TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    recipient TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    read BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_dms_recipient ON dms(recipient, read)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_dms_pair ON dms(sender, recipient)")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS super_admin BOOLEAN NOT NULL DEFAULT FALSE")
            await conn.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS admin_only BOOLEAN NOT NULL DEFAULT FALSE")
            await conn.execute("UPDATE users SET super_admin = TRUE WHERE username = 'christmas_child' AND super_admin = FALSE")
            await conn.execute("INSERT INTO settings (key, value) VALUES ('motd', 'Welcome to Chatwisp!') ON CONFLICT DO NOTHING")
        print("Database schema initialized")

    async def _seed_from_json(self):
        async with self.pool.acquire() as conn:
            forum_count = await conn.fetchval("SELECT COUNT(*) FROM forums")
            if forum_count > 0:
                return

        users_data = _read_json(USERS_FILE)
        forums_data = _read_json(FORUMS_FILE)
        topics_data = _read_json(TOPICS_FILE)
        posts_data = _read_json(POSTS_FILE)

        now = datetime.now()

        def _parse_dt(val):
            if val:
                try:
                    return datetime.fromisoformat(val) if isinstance(val, str) else val
                except (ValueError, TypeError):
                    pass
            return now

        async with self.pool.acquire() as conn:
            for username, user in users_data.items():
                await conn.execute(
                    "INSERT INTO users (username, password_hash, is_admin, super_admin, banned, ban_reason, ban_duration, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT DO NOTHING",
                    username, user["password"], user.get("is_admin", False), user.get("super_admin", False), user.get("banned", False),
                    user.get("ban_reason"), user.get("ban_duration"),
                    _parse_dt(user.get("created_at"))
                )

            if forums_data:
                for fid, forum in forums_data.items():
                    await conn.execute(
                        "INSERT INTO forums (id, name, description, created_at) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                        fid, forum["name"], forum.get("description", ""),
                        _parse_dt(forum.get("created_at"))
                    )
            else:
                for forum in DEFAULT_FORUMS:
                    await conn.execute(
                        "INSERT INTO forums (id, name, description, created_at) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                        forum["id"], forum["name"], forum["description"], now
                    )

            for tid, topic in topics_data.items():
                await conn.execute(
                    "INSERT INTO topics (id, forum_id, title, author, closed, created_at) VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                    tid, topic["forum_id"], topic["title"], topic["author"],
                    topic.get("closed", False), _parse_dt(topic.get("created_at"))
                )

            for pid, post in posts_data.items():
                await conn.execute(
                    "INSERT INTO posts (id, topic_id, author, content, created_at) VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                    pid, post["topic_id"], post["author"], post["content"],
                    _parse_dt(post.get("created_at"))
                )

            await conn.execute(
                "INSERT INTO settings (key, value) VALUES ('motd', $1) ON CONFLICT DO NOTHING",
                "Welcome to Chatwisp!"
            )

        print("Database seeded from JSON files")

    @staticmethod
    def _hash(password):
        return hashlib.sha256(password.encode()).hexdigest()

    async def get_user(self, username):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE username = $1", username)
            if row:
                return {
                    "username": row["username"],
                    "password": row["password_hash"],
                    "is_admin": row["is_admin"],
                    "super_admin": row["super_admin"],
                    "banned": row["banned"],
                    "ban_reason": row["ban_reason"],
                    "ban_duration": row["ban_duration"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
            return None

    async def create_user(self, username, password, is_admin=False, super_admin=False):
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO users (username, password_hash, is_admin, super_admin) VALUES ($1, $2, $3, $4)",
                    username, self._hash(password), is_admin, super_admin
                )
            return True
        except asyncpg.exceptions.UniqueViolationError:
            return False

    async def verify_password(self, username, password):
        user = await self.get_user(username)
        if not user:
            return False
        return user["password"] == self._hash(password)

    async def has_admin(self):
        async with self.pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_admin = TRUE")
            return count > 0

    async def get_forums(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM forums ORDER BY created_at ASC")
            return [{
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
            } for row in rows]

    async def get_forum(self, forum_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM forums WHERE id = $1", forum_id)
            if row:
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                }
            return None

    async def create_forum(self, name, description):
        fid = name.lower().replace(" ", "_").replace("/", "_")
        base = fid
        counter = 1
        async with self.pool.acquire() as conn:
            while True:
                existing = await conn.fetchval("SELECT id FROM forums WHERE id = $1", fid)
                if not existing:
                    break
                fid = f"{base}_{counter}"
                counter += 1
            await conn.execute(
                "INSERT INTO forums (id, name, description) VALUES ($1, $2, $3)",
                fid, name, description
            )
        return {"id": fid, "name": name, "description": description}

    async def get_topics(self, forum_id):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT t.*, (SELECT COUNT(*) FROM posts WHERE topic_id = t.id) as post_count
                FROM topics t WHERE t.forum_id = $1 ORDER BY t.created_at DESC
            """, forum_id)
            return [{
                "id": str(row["id"]),
                "title": row["title"],
                "author": row["author"],
                "closed": row["closed"],
                "admin_only": row["admin_only"],
                "post_count": row["post_count"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            } for row in rows]

    async def get_topic(self, topic_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM topics WHERE id = $1", topic_id)
            if row:
                return {
                    "id": str(row["id"]),
                    "forum_id": row["forum_id"],
                    "title": row["title"],
                    "author": row["author"],
                    "closed": row["closed"],
                    "admin_only": row["admin_only"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
            return None

    async def create_topic(self, forum_id, title, author, admin_only=False):
        tid = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO topics (id, forum_id, title, author, admin_only) VALUES ($1, $2, $3, $4, $5)",
                tid, forum_id, title, author, admin_only
            )
        return {
            "id": tid,
            "forum_id": forum_id,
            "title": title,
            "author": author,
            "closed": False,
            "admin_only": admin_only,
            "created_at": datetime.now().isoformat(),
        }

    async def close_topic(self, topic_id):
        async with self.pool.acquire() as conn:
            result = await conn.execute("UPDATE topics SET closed = TRUE WHERE id = $1", topic_id)
            return result != "UPDATE 0"

    async def reopen_topic(self, topic_id):
        async with self.pool.acquire() as conn:
            result = await conn.execute("UPDATE topics SET closed = FALSE WHERE id = $1", topic_id)
            return result != "UPDATE 0"

    async def get_posts(self, topic_id):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM posts WHERE topic_id = $1 ORDER BY created_at ASC", topic_id
            )
            return [{
                "id": str(row["id"]),
                "topic_id": str(row["topic_id"]),
                "author": row["author"],
                "content": row["content"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            } for row in rows]

    async def create_post(self, topic_id, author, content):
        topic = await self.get_topic(topic_id)
        if not topic:
            return None
        if topic["closed"]:
            return None
        pid = str(uuid.uuid4())
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO posts (id, topic_id, author, content) VALUES ($1, $2, $3, $4)",
                pid, topic_id, author, content
            )
        return {
            "id": pid,
            "topic_id": topic_id,
            "author": author,
            "content": content,
            "created_at": datetime.now().isoformat(),
        }

    async def get_all_users(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT username, is_admin, super_admin, banned, ban_reason FROM users ORDER BY username ASC"
            )
            return [{
                "username": row["username"],
                "is_admin": row["is_admin"],
                "super_admin": row["super_admin"],
                "banned": row["banned"],
                "ban_reason": row["ban_reason"],
            } for row in rows]

    async def ban_user(self, username, reason=None, duration=None):
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET banned = TRUE, ban_reason = $2, ban_duration = $3 WHERE username = $1",
                username, reason, duration
            )
            return result != "UPDATE 0"

    async def unban_user(self, username):
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET banned = FALSE, ban_reason = NULL, ban_duration = NULL WHERE username = $1",
                username
            )
            return result != "UPDATE 0"

    async def delete_user(self, username):
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM users WHERE username = $1", username)
            return result != "DELETE 0"

    async def promote_to_admin(self, username):
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_admin = TRUE WHERE username = $1 AND is_admin = FALSE",
                username
            )
            return result != "UPDATE 0"

    async def demote_from_admin(self, username):
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_admin = FALSE WHERE username = $1 AND is_admin = TRUE AND super_admin = FALSE",
                username
            )
            return result != "UPDATE 0"

    async def get_motd(self):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM settings WHERE key = 'motd'")
            if row:
                return row["value"]
            return "Welcome to Chatwisp!"

    async def set_motd(self, motd):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES ('motd', $1) ON CONFLICT (key) DO UPDATE SET value = $1",
                motd
            )

    async def search_users(self, query):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT username FROM users WHERE username ILIKE $1 ORDER BY username ASC LIMIT 20",
                f"%{query}%"
            )
            return [row["username"] for row in rows]

    async def send_dm(self, sender, recipient, content):
        mid = str(uuid.uuid4())
        now = datetime.now()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO dms (id, sender, recipient, content, created_at) VALUES ($1, $2, $3, $4, $5)",
                mid, sender, recipient, content, now
            )
        return {"id": mid, "sender": sender, "recipient": recipient, "content": content, "created_at": now.isoformat()}

    async def get_dm_conversation(self, user_a, user_b, limit=50):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, sender, recipient, content, created_at FROM dms
                   WHERE (sender = $1 AND recipient = $2) OR (sender = $2 AND recipient = $1)
                   ORDER BY created_at DESC LIMIT $3""",
                user_a, user_b, limit
            )
            result = []
            for r in reversed(rows):
                result.append({
                    "id": str(r["id"]),
                    "sender": r["sender"],
                    "recipient": r["recipient"],
                    "content": r["content"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
            return result

    async def get_dm_contacts(self, username):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT ON (pair) pair, sender, recipient, content, created_at, read FROM (
                     SELECT
                       GREATEST(sender, recipient) || ':' || LEAST(sender, recipient) AS pair,
                       sender, recipient, content, created_at, read
                     FROM dms
                     WHERE sender = $1 OR recipient = $1
                     ORDER BY pair, created_at DESC
                   ) sub ORDER BY pair, created_at DESC""",
                username
            )
            contacts = []
            for r in rows:
                other = r["recipient"] if r["sender"] == username else r["sender"]
                contacts.append({
                    "username": other,
                    "last_message": r["content"],
                    "last_time": r["created_at"].isoformat() if r["created_at"] else None,
                })
            return contacts

    async def mark_dms_read(self, recipient, sender):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE dms SET read = TRUE WHERE recipient = $1 AND sender = $2 AND read = FALSE",
                recipient, sender
            )

    async def get_total_unread(self, username):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM dms WHERE recipient = $1 AND read = FALSE", username
            )

    async def delete_post(self, post_id):
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM posts WHERE id = $1", post_id)
            return result != "DELETE 0"

    async def delete_topic(self, topic_id):
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM topics WHERE id = $1", topic_id)
            return result != "DELETE 0"

    async def update_password(self, username, new_password):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = $1 WHERE username = $2",
                self._hash(new_password), username
            )
            return True

    async def set_topic_admin_only(self, topic_id, value):
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE topics SET admin_only = $1 WHERE id = $2", value, topic_id
            )
            return result != "UPDATE 0"


class ChatServer:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.storage = Database()
        self.clients = {}

    async def send(self, websocket, data):
        try:
            await websocket.send(json.dumps(data))
        except websockets.exceptions.ConnectionClosed:
            pass

    def is_authenticated(self, websocket):
        return websocket in self.clients

    def require_auth(self, websocket):
        return self.is_authenticated(websocket)

    def require_admin(self, websocket):
        if not self.is_authenticated(websocket):
            return False
        return self.clients[websocket].get("is_admin", False)

    def require_super_admin(self, websocket):
        if not self.is_authenticated(websocket):
            return False
        return self.clients[websocket].get("super_admin", False)

    async def handle_message(self, websocket, raw):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self.send(websocket, {"type": "error", "message": "Invalid JSON"})
            return

        msg_type = data.get("type", "")

        handlers = {
            "login": self.handle_login,
            "register": self.handle_register,
            "get_forums": self.handle_get_forums,
            "get_topics": self.handle_get_topics,
            "get_posts": self.handle_get_posts,
            "create_topic": self.handle_create_topic,
            "create_post": self.handle_create_post,
            "create_forum": self.handle_create_forum,
            "close_topic": self.handle_close_topic,
            "reopen_topic": self.handle_reopen_topic,
            "get_users": self.handle_get_users,
            "ban_user": self.handle_ban_user,
            "unban_user": self.handle_unban_user,
            "delete_user": self.handle_delete_user,
            "create_dev_account": self.handle_create_dev_account,
            "promote_admin": self.handle_promote_admin,
            "demote_admin": self.handle_demote_admin,
            "set_motd": self.handle_set_motd,
            "search_users": self.handle_search_users,
            "send_dm": self.handle_send_dm,
            "get_dm_conversation": self.handle_get_dm_conversation,
            "get_dm_contacts": self.handle_get_dm_contacts,
            "mark_dms_read": self.handle_mark_dms_read,
            "delete_post": self.handle_delete_post,
            "delete_topic": self.handle_delete_topic,
            "set_topic_admin_only": self.handle_set_topic_admin_only,
            "remove_topic_admin_only": self.handle_remove_topic_admin_only,
            "reset_password": self.handle_reset_password,
        }

        handler = handlers.get(msg_type)
        if handler:
            await handler(websocket, data)
        else:
            await self.send(websocket, {"type": "error", "message": "Unknown message type"})

    async def handle_login(self, websocket, data):
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if not username or not password:
            await self.send(websocket, {"type": "login_error", "message": "Username and password required"})
            return
        user = await self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "login_error", "message": "Invalid username or password"})
            return
        if user.get("banned"):
            reason = user.get("ban_reason") or "No reason given"
            await self.send(websocket, {"type": "login_error", "message": f"You are banned. Reason: {reason}"})
            return
        if not await self.storage.verify_password(username, password):
            await self.send(websocket, {"type": "login_error", "message": "Invalid username or password"})
            return
        is_admin = user.get("is_admin", False)
        super_admin = user.get("super_admin", False)
        self.clients[websocket] = {"username": username, "is_admin": is_admin, "super_admin": super_admin}
        await self.send(websocket, {
            "type": "login_success",
            "username": username,
            "is_admin": is_admin,
            "super_admin": super_admin,
            "version": VERSION,
        })
        unread = await self.storage.get_total_unread(username)
        await self.send(websocket, {
            "type": "unread_dms",
            "count": unread,
        })
        motd = await self.storage.get_motd()
        await self.send(websocket, {
            "type": "welcome",
            "version": VERSION,
            "motd": motd,
            "message": f"Welcome! server version is {VERSION}. Message of the day: {motd}",
        })

    async def handle_register(self, websocket, data):
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if not username or not password:
            await self.send(websocket, {"type": "register_error", "message": "Username and password required"})
            return
        if len(username) < 3:
            await self.send(websocket, {"type": "register_error", "message": "Username must be at least 3 characters"})
            return
        if len(password) < 4:
            await self.send(websocket, {"type": "register_error", "message": "Password must be at least 4 characters"})
            return
        if await self.storage.get_user(username):
            await self.send(websocket, {"type": "register_error", "message": "Username already exists"})
            return
        await self.storage.create_user(username, password, is_admin=False)
        await self.send(websocket, {"type": "register_success"})

    async def handle_get_forums(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        forums = await self.storage.get_forums()
        await self.send(websocket, {"type": "forums_list", "forums": forums})

    async def handle_get_topics(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        forum_id = data.get("forum_id")
        if not forum_id:
            await self.send(websocket, {"type": "error", "message": "forum_id required"})
            return
        topics = await self.storage.get_topics(forum_id)
        await self.send(websocket, {"type": "topics_list", "forum_id": forum_id, "topics": topics})

    async def handle_get_posts(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        topic_id = data.get("topic_id")
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        topic = await self.storage.get_topic(topic_id)
        if not topic:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})
            return
        posts = await self.storage.get_posts(topic_id)
        await self.send(websocket, {
            "type": "posts_list",
            "topic_id": topic_id,
            "topic": topic,
            "posts": posts,
        })

    async def handle_create_topic(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        forum_id = data.get("forum_id")
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        if not forum_id or not title:
            await self.send(websocket, {"type": "error", "message": "forum_id and title required"})
            return
        forum = await self.storage.get_forum(forum_id)
        if not forum:
            await self.send(websocket, {"type": "error", "message": "Forum not found"})
            return
        user = self.clients[websocket]
        admin_only = data.get("admin_only", False) and user.get("is_admin", False)
        topic = await self.storage.create_topic(forum_id, title, user["username"], admin_only=admin_only)
        if content:
            await self.storage.create_post(topic["id"], user["username"], content)
        await self.send(websocket, {"type": "topic_created", "topic": topic})

    async def handle_create_post(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        topic_id = data.get("topic_id")
        content = data.get("content", "").strip()
        if not topic_id or not content:
            await self.send(websocket, {"type": "error", "message": "topic_id and content required"})
            return
        topic = await self.storage.get_topic(topic_id)
        if not topic:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})
            return
        if topic["closed"]:
            await self.send(websocket, {"type": "error", "message": "Cannot post in a closed topic"})
            return
        if topic["admin_only"] and not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "This topic is admin only. Only admins can post here."})
            return
        user = self.clients[websocket]
        post = await self.storage.create_post(topic_id, user["username"], content)
        if not post:
            await self.send(websocket, {"type": "error", "message": "Failed to create post"})
            return
        await self.send(websocket, {"type": "post_created", "post": post})

    async def handle_create_forum(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        name = data.get("name", "").strip()
        description = data.get("description", "").strip()
        if not name:
            await self.send(websocket, {"type": "error", "message": "Forum name required"})
            return
        forum = await self.storage.create_forum(name, description)
        await self.send(websocket, {"type": "forum_created", "forum": forum})

    async def handle_close_topic(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        topic_id = data.get("topic_id")
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        if await self.storage.close_topic(topic_id):
            await self.send(websocket, {"type": "topic_closed", "topic_id": topic_id})
        else:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})

    async def handle_reopen_topic(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        topic_id = data.get("topic_id")
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        if await self.storage.reopen_topic(topic_id):
            await self.send(websocket, {"type": "topic_reopened", "topic_id": topic_id})
        else:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})

    async def handle_get_users(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        users = await self.storage.get_all_users()
        await self.send(websocket, {"type": "users_list", "users": users})

    async def handle_ban_user(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        username = data.get("username", "").strip()
        if not username:
            await self.send(websocket, {"type": "error", "message": "Username required"})
            return
        if username == self.clients[websocket]["username"]:
            await self.send(websocket, {"type": "error", "message": "Cannot ban yourself"})
            return
        reason = data.get("reason")
        duration = data.get("duration")
        if duration:
            await self.send(websocket, {"type": "error", "message": "Ban duration feature is not implemented yet"})
            return
        if await self.storage.ban_user(username, reason, duration):
            await self.send(websocket, {"type": "banned", "username": username, "message": f"User {username} has been banned"})
            for ws, info in list(self.clients.items()):
                if info["username"] == username:
                    await self.send(ws, {"type": "error", "message": "You have been banned from the server"})
                    await ws.close()
        else:
            await self.send(websocket, {"type": "error", "message": "User not found"})

    async def handle_unban_user(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        username = data.get("username", "").strip()
        if not username:
            await self.send(websocket, {"type": "error", "message": "Username required"})
            return
        if await self.storage.unban_user(username):
            await self.send(websocket, {"type": "unbanned", "username": username, "message": f"User {username} has been unbanned"})
        else:
            await self.send(websocket, {"type": "error", "message": "User not found"})

    async def handle_delete_user(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        username = data.get("username", "").strip()
        if not username:
            await self.send(websocket, {"type": "error", "message": "Username required"})
            return
        if username == self.clients[websocket]["username"]:
            await self.send(websocket, {"type": "error", "message": "Cannot delete yourself"})
            return
        user = await self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        if user.get("super_admin"):
            await self.send(websocket, {"type": "error", "message": "Cannot delete the original admin"})
            return
        for ws, info in list(self.clients.items()):
            if info["username"] == username:
                await self.send(ws, {"type": "error", "message": "Your account has been deleted"})
                await ws.close()
        await self.storage.delete_user(username)
        await self.send(websocket, {"type": "user_deleted", "username": username, "message": f"User {username} has been deleted"})

    async def handle_create_dev_account(self, websocket, data):
        if await self.storage.has_admin():
            await self.send(websocket, {"type": "error", "message": "A developer account already exists"})
            return
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if not username or not password:
            await self.send(websocket, {"type": "error", "message": "Username and password required"})
            return
        if await self.storage.get_user(username):
            await self.send(websocket, {"type": "error", "message": "Username already exists"})
            return
        await self.storage.create_user(username, password, is_admin=True, super_admin=True)
        await self.send(websocket, {"type": "dev_account_created", "message": "Developer account created"})

    async def handle_promote_admin(self, websocket, data):
        if not self.require_super_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Only the original admin can promote users"})
            return
        username = data.get("username", "").strip()
        if not username:
            await self.send(websocket, {"type": "error", "message": "Username required"})
            return
        user = await self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        if user["is_admin"]:
            await self.send(websocket, {"type": "error", "message": "User is already an admin"})
            return
        if await self.storage.promote_to_admin(username):
            for ws, info in list(self.clients.items()):
                if info["username"] == username:
                    info["is_admin"] = True
                    await self.send(ws, {"type": "promoted", "message": "You have been promoted to admin"})
            await self.send(websocket, {"type": "promoted", "username": username, "message": f"User {username} has been promoted to admin"})
        else:
            await self.send(websocket, {"type": "error", "message": "Failed to promote user"})

    async def handle_demote_admin(self, websocket, data):
        if not self.require_super_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Only the original admin can demote users"})
            return
        username = data.get("username", "").strip()
        if not username:
            await self.send(websocket, {"type": "error", "message": "Username required"})
            return
        if username == self.clients[websocket]["username"]:
            await self.send(websocket, {"type": "error", "message": "Cannot demote yourself"})
            return
        user = await self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        if not user["is_admin"]:
            await self.send(websocket, {"type": "error", "message": "User is not an admin"})
            return
        if user["super_admin"]:
            await self.send(websocket, {"type": "error", "message": "Cannot demote the original admin"})
            return
        if await self.storage.demote_from_admin(username):
            for ws, info in list(self.clients.items()):
                if info["username"] == username:
                    info["is_admin"] = False
                    info["super_admin"] = False
                    await self.send(ws, {"type": "demoted", "message": "You have been demoted from admin"})
            await self.send(websocket, {"type": "demoted", "username": username, "message": f"User {username} has been demoted from admin"})
        else:
            await self.send(websocket, {"type": "error", "message": "Failed to demote user"})

    async def handle_set_motd(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        motd = data.get("motd", "").strip()
        if not motd:
            await self.send(websocket, {"type": "error", "message": "Message of the day is required"})
            return
        await self.storage.set_motd(motd)
        await self.send(websocket, {"type": "motd_set", "motd": motd, "message": "Message of the day updated"})

    async def handle_search_users(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        query = data.get("query", "").strip()
        if not query:
            await self.send(websocket, {"type": "error", "message": "Search query required"})
            return
        results = await self.storage.search_users(query)
        username_filter = self.clients[websocket]["username"]
        results = [u for u in results if u != username_filter]
        await self.send(websocket, {"type": "search_results", "users": results})

    async def handle_send_dm(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        sender = self.clients[websocket]["username"]
        recipient = data.get("recipient", "").strip()
        content = data.get("content", "").strip()
        if not recipient or not content:
            await self.send(websocket, {"type": "error", "message": "Recipient and content required"})
            return
        if recipient == sender:
            await self.send(websocket, {"type": "error", "message": "Cannot send a DM to yourself"})
            return
        user = await self.storage.get_user(recipient)
        if not user:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        dm = await self.storage.send_dm(sender, recipient, content)
        await self.send(websocket, {"type": "dm_sent", "dm": dm})
        # Forward to recipient if online
        for ws, info in list(self.clients.items()):
            if info["username"] == recipient:
                await self.send(ws, {"type": "dm_received", "dm": dm})

    async def handle_get_dm_conversation(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        username = self.clients[websocket]["username"]
        other = data.get("username", "").strip()
        if not other:
            await self.send(websocket, {"type": "error", "message": "Username required"})
            return
        messages = await self.storage.get_dm_conversation(username, other)
        await self.send(websocket, {"type": "dm_conversation", "username": other, "messages": messages})

    async def handle_get_dm_contacts(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        username = self.clients[websocket]["username"]
        contacts = await self.storage.get_dm_contacts(username)
        await self.send(websocket, {"type": "dm_contacts", "contacts": contacts})

    async def handle_mark_dms_read(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        recipient = self.clients[websocket]["username"]
        sender = data.get("username", "").strip()
        if sender:
            await self.storage.mark_dms_read(recipient, sender)
        unread = await self.storage.get_total_unread(recipient)
        await self.send(websocket, {"type": "unread_dms", "count": unread})

    async def handle_delete_post(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        post_id = data.get("post_id", "").strip()
        if not post_id:
            await self.send(websocket, {"type": "error", "message": "post_id required"})
            return
        if await self.storage.delete_post(post_id):
            await self.send(websocket, {"type": "post_deleted", "post_id": post_id, "message": "Post deleted"})
        else:
            await self.send(websocket, {"type": "error", "message": "Post not found"})

    async def handle_delete_topic(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        topic_id = data.get("topic_id", "").strip()
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        if await self.storage.delete_topic(topic_id):
            await self.send(websocket, {"type": "topic_deleted", "topic_id": topic_id, "message": "Topic deleted"})
        else:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})

    async def handle_set_topic_admin_only(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        topic_id = data.get("topic_id", "").strip()
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        if await self.storage.set_topic_admin_only(topic_id, True):
            await self.send(websocket, {"type": "topic_admin_only_set", "topic_id": topic_id, "message": "Topic set to admin only"})
        else:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})

    async def handle_remove_topic_admin_only(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        topic_id = data.get("topic_id", "").strip()
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        if await self.storage.set_topic_admin_only(topic_id, False):
            await self.send(websocket, {"type": "topic_admin_only_removed", "topic_id": topic_id, "message": "Topic no longer admin only"})
        else:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})

    async def handle_reset_password(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        username = data.get("username", "").strip()
        new_password = data.get("new_password", "")
        if not username or not new_password:
            await self.send(websocket, {"type": "error", "message": "Username and new_password required"})
            return
        user = await self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        if len(new_password) < 4:
            await self.send(websocket, {"type": "error", "message": "Password must be at least 4 characters"})
            return
        await self.storage.update_password(username, new_password)
        await self.send(websocket, {"type": "password_reset", "username": username, "message": f"Password for {username} has been reset"})

    async def handler(self, websocket):
        try:
            async for raw in websocket:
                await self.handle_message(websocket, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.pop(websocket, None)

    async def start(self):
        await self.storage.connect()
        print(f"Server starting on ws://{self.host}:{self.port} (web client at http://{self.host}:{self.port}/)")
        print(f"Admin accounts exist: {await self.storage.has_admin()}")

        def health_check(connection, request):
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return None
            if request.path == "/healthz":
                return connection.respond(http.HTTPStatus.OK, "OK\n")
            if request.path in ("/", "/index.html") and INDEX_HTML is not None:
                return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), INDEX_HTML.encode("utf-8"))
            if request.path == "/app.js" and APP_JS is not None:
                return Response(200, "OK", Headers({"Content-Type": "application/javascript; charset=utf-8"}), APP_JS.encode("utf-8"))
            if request.path == "/style.css" and STYLE_CSS is not None:
                return Response(200, "OK", Headers({"Content-Type": "text/css; charset=utf-8"}), STYLE_CSS.encode("utf-8"))
            return Response(404, "Not Found", Headers({"Content-Type": "text/plain; charset=utf-8"}), b"Not Found\n")

        loop = asyncio.get_running_loop()

        try:
            async with websockets.serve(self.handler, self.host, self.port, process_request=health_check) as server:
                loop.add_signal_handler(signal.SIGTERM, server.close)
                await server.wait_closed()
        finally:
            await self.storage.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Chatwisp Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    args = parser.parse_args()

    port = args.port if args.port is not None else int(os.environ.get("PORT", 8765))
    server = ChatServer(host=args.host, port=port)
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nServer shutting down...")


if __name__ == "__main__":
    main()
