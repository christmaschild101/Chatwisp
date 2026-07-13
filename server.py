#!/usr/bin/env python3
import asyncio
import http
import json
import hashlib
import os
import secrets
import signal
import time
import uuid
import sys
import bcrypt
from datetime import datetime, timedelta, timezone

VERSION = "4.0.0"

SERVER_START_TIME = time.time()
BOT_USERNAME = "Chatwisp Official Account"
MINIMUM_CLIENT_VERSION = "4.0.0"
DOWNLOAD_URL = "https://chatwisp-sight.onrender.com/"

try:
    import websockets
    from websockets.http11 import Response
    from websockets.datastructures import Headers
except ImportError:
    print("Error: websockets library not found. Run: pip install websockets")
    sys.exit(1)

_HAS_PG = False
try:
    import asyncpg
    _HAS_PG = True
except ImportError:
    pass

_HAS_SQLITE = False
try:
    import aiosqlite
    _HAS_SQLITE = True
except ImportError:
    pass

if not _HAS_PG and not _HAS_SQLITE:
    print("Error: neither asyncpg nor aiosqlite found. Run: pip install -r requirements.txt")
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
MANIFEST_JSON = _read_static("manifest.json")
SW_JS = _read_static("sw.js")

MUSIC_DIR = os.path.join(CLIENT_WEB_DIR, "music")
AVAILABLE_SONGS = ["ByTheFire", "Frozen-in-Time", "Noisescape", "TranquilReflections", "Wonder"]

def _read_json(path, default=None):
    if default is None:
        default = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _detect_db_mode():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("PGDATABASE_URL")
    if dsn:
        return "postgres"
    host = os.environ.get("PGHOST")
    user = os.environ.get("PGUSER")
    password = os.environ.get("PGPASSWORD")
    if host and user and password:
        return "postgres"
    return "sqlite"


def _parse_pg_dsn(dsn):
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


class _PostgresBackend:
    def __init__(self):
        self.pool = None

    async def connect(self):
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("PGDATABASE_URL")
        if dsn:
            kwargs = _parse_pg_dsn(dsn)
        else:
            kwargs = {
                "host": os.environ["PGHOST"],
                "port": int(os.environ.get("PGPORT", 5432)),
                "user": os.environ["PGUSER"],
                "password": os.environ["PGPASSWORD"],
                "database": os.environ.get("PGDATABASE", "postgres"),
                "min_size": 1,
                "max_size": 3,
            }
        safe = {**kwargs, "password": "***"}
        print(f"Connecting to PostgreSQL: host={safe.get('host')} port={safe.get('port')} user={safe.get('user')} database={safe.get('database')}", flush=True)
        for attempt in range(5):
            try:
                self.pool = await asyncpg.create_pool(**kwargs)
                break
            except asyncpg.InvalidPasswordError:
                raise
            except Exception as e:
                print(f"DB connection attempt {attempt+1} failed: {e}", flush=True)
                if attempt < 4:
                    await asyncio.sleep(3)
        else:
            raise RuntimeError("Could not connect to database after 5 attempts")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def execute(self, sql, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchrow(self, sql, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def fetchval(self, sql, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    @staticmethod
    def row_to_dict(row, columns=None):
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def rows_to_list(rows):
        return [dict(r) for r in rows]

    @staticmethod
    def status_matches(result, prefix):
        return result and result.startswith(prefix)

    @staticmethod
    def placeholder():
        return "$"


class _SqliteBackend:
    def __init__(self):
        self.db = None

    async def connect(self):
        db_path = os.environ.get("SQLITE_PATH", "chatwisp.db")
        print(f"Using SQLite database: {db_path}", flush=True)
        self.db = await aiosqlite.connect(db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")

    async def close(self):
        if self.db:
            await self.db.close()

    def _sql(self, sql):
        import re
        return re.sub(r'\$(\d+)', '?', sql)

    async def execute(self, sql, *args):
        cursor = await self.db.execute(self._sql(sql), args)
        await self.db.commit()
        return cursor.rowcount

    async def fetchrow(self, sql, *args):
        cursor = await self.db.execute(self._sql(sql), args)
        row = await cursor.fetchone()
        return row

    async def fetch(self, sql, *args):
        cursor = await self.db.execute(self._sql(sql), args)
        rows = await cursor.fetchall()
        return rows

    async def fetchval(self, sql, *args):
        row = await self.fetchrow(sql, *args)
        if row:
            return row[0]
        return None

    @staticmethod
    def row_to_dict(row, columns=None):
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def rows_to_list(rows):
        return [dict(r) for r in rows]

    @staticmethod
    def status_matches(result, prefix):
        if prefix == "UPDATE 0":
            return result == 0
        if prefix.startswith("UPDATE"):
            return result > 0
        if prefix == "DELETE 0":
            return result == 0
        if prefix.startswith("DELETE"):
            return result > 0
        if prefix == "INSERT 0":
            return result == 0
        if prefix.startswith("INSERT"):
            return result > 0
        return False

    @staticmethod
    def placeholder():
        return "?"


class Database:
    def __init__(self):
        self.backend = None
        self._mode = _detect_db_mode()

    async def connect(self):
        if self._mode == "postgres":
            if not _HAS_PG:
                raise RuntimeError("PostgreSQL mode requires asyncpg. Run: pip install asyncpg")
            self.backend = _PostgresBackend()
        else:
            if not _HAS_SQLITE:
                raise RuntimeError("SQLite mode requires aiosqlite. Run: pip install aiosqlite")
            self.backend = _SqliteBackend()
        await self.backend.connect()
        await self._init_schema()
        await self._seed_from_json()

    async def close(self):
        if self.backend:
            await self.backend.close()

    async def _init_schema(self):
        p = self.backend.placeholder()
        if self._mode == "postgres":
            await self.backend.execute(f"""
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
            await self.backend.execute(f"""
                CREATE TABLE IF NOT EXISTS forums (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await self.backend.execute(f"""
                CREATE TABLE IF NOT EXISTS topics (
                    id UUID PRIMARY KEY,
                    forum_id TEXT NOT NULL REFERENCES forums(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    closed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await self.backend.execute(f"""
                CREATE TABLE IF NOT EXISTS posts (
                    id UUID PRIMARY KEY,
                    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                    author TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await self.backend.execute(f"""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await self.backend.execute(f"""
                CREATE TABLE IF NOT EXISTS dms (
                    id UUID PRIMARY KEY,
                    sender TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    recipient TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    read BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            await self.backend.execute("CREATE INDEX IF NOT EXISTS idx_dms_recipient ON dms(recipient, read)")
            await self.backend.execute("CREATE INDEX IF NOT EXISTS idx_dms_pair ON dms(sender, recipient)")
            await self.backend.execute(f"""
                CREATE TABLE IF NOT EXISTS voice_channels (
                    id TEXT PRIMARY KEY,
                    forum_id TEXT NOT NULL REFERENCES forums(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await self.backend.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS super_admin BOOLEAN NOT NULL DEFAULT FALSE")
            await self.backend.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS admin_only BOOLEAN NOT NULL DEFAULT FALSE")
            await self.backend.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS slug TEXT")
            slugless = await self.backend.fetch(f"SELECT id FROM topics WHERE slug IS NULL OR slug = ''")
            for row in self.backend.rows_to_list(slugless):
                await self.backend.execute(f"UPDATE topics SET slug = {p}1 WHERE id = {p}2", secrets.token_hex(12), row["id"])
            if slugless:
                print(f"Assigned slugs to {len(slugless)} existing topics")
            await self.backend.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_until TIMESTAMPTZ")
            await self.backend.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot BOOLEAN NOT NULL DEFAULT FALSE")
            await self.backend.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS signature TEXT NOT NULL DEFAULT ''")
            await self.backend.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS music_prefs TEXT NOT NULL DEFAULT '{}'")
            await self.backend.execute(f"UPDATE users SET super_admin = TRUE WHERE username = 'christmas_child' AND super_admin = FALSE")
            await self.backend.execute(f"INSERT INTO settings (key, value) VALUES ('motd', 'Welcome to Chatwisp!') ON CONFLICT DO NOTHING")
            bot = await self.backend.fetchrow(f"SELECT username FROM users WHERE username = {p}1", BOT_USERNAME)
            if not bot:
                random_hash = hashlib.sha256((str(uuid.uuid4()) * 8).encode()).hexdigest()
                await self.backend.execute(
                    f"INSERT INTO users (username, password_hash, is_admin, super_admin, banned, is_bot) VALUES ({p}1, {p}2, TRUE, FALSE, FALSE, TRUE)",
                    BOT_USERNAME, random_hash
                )
        else:
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    super_admin INTEGER NOT NULL DEFAULT 0,
                    banned INTEGER NOT NULL DEFAULT 0,
                    ban_reason TEXT,
                    ban_duration TEXT,
                    ban_until TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    signature TEXT NOT NULL DEFAULT '',
                    music_prefs TEXT NOT NULL DEFAULT '{}'
                )
            """)
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS forums (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS topics (
                    id TEXT PRIMARY KEY,
                    forum_id TEXT NOT NULL REFERENCES forums(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    closed INTEGER NOT NULL DEFAULT 0,
                    admin_only INTEGER NOT NULL DEFAULT 0,
                    slug TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                    author TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS dms (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    recipient TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    read INTEGER NOT NULL DEFAULT 0
                )
            """)
            await self.backend.execute("CREATE INDEX IF NOT EXISTS idx_dms_recipient ON dms(recipient, read)")
            await self.backend.execute("CREATE INDEX IF NOT EXISTS idx_dms_pair ON dms(sender, recipient)")
            await self.backend.execute("""
                CREATE TABLE IF NOT EXISTS voice_channels (
                    id TEXT PRIMARY KEY,
                    forum_id TEXT NOT NULL REFERENCES forums(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await self.backend.execute(f"UPDATE users SET super_admin = 1 WHERE username = 'christmas_child' AND super_admin = 0")
            await self.backend.execute(f"INSERT OR IGNORE INTO settings (key, value) VALUES ('motd', 'Welcome to Chatwisp!')")
            bot = await self.backend.fetchrow(f"SELECT username FROM users WHERE username = ?", BOT_USERNAME)
            if not bot:
                random_hash = hashlib.sha256((str(uuid.uuid4()) * 8).encode()).hexdigest()
                await self.backend.execute(
                    "INSERT INTO users (username, password_hash, is_admin, super_admin, banned, is_bot) VALUES (?, ?, 1, 0, 0, 1)",
                    BOT_USERNAME, random_hash
                )
        print("Database schema initialized")

    async def _seed_from_json(self):
        count = await self.backend.fetchval("SELECT COUNT(*) FROM forums")
        if count and count > 0:
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

        p = self.backend.placeholder()

        for username, user in users_data.items():
            banned = 1 if user.get("banned") else 0
            is_admin = 1 if user.get("is_admin") else 0
            super_admin = 1 if user.get("super_admin") else 0
            if self._mode == "sqlite":
                await self.backend.execute(
                    f"INSERT OR IGNORE INTO users (username, password_hash, is_admin, super_admin, banned, ban_reason, ban_duration, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5, {p}6, {p}7, {p}8)",
                    username, user["password"], is_admin, super_admin, banned,
                    user.get("ban_reason"), user.get("ban_duration"),
                    _parse_dt(user.get("created_at")).isoformat()
                )
            else:
                await self.backend.execute(
                    f"INSERT INTO users (username, password_hash, is_admin, super_admin, banned, ban_reason, ban_duration, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5, {p}6, {p}7, {p}8) ON CONFLICT DO NOTHING",
                    username, user["password"], user.get("is_admin", False), user.get("super_admin", False), user.get("banned", False),
                    user.get("ban_reason"), user.get("ban_duration"),
                    _parse_dt(user.get("created_at"))
                )

        if forums_data:
            for fid, forum in forums_data.items():
                if self._mode == "sqlite":
                    await self.backend.execute(
                        f"INSERT OR IGNORE INTO forums (id, name, description, created_at) VALUES ({p}1, {p}2, {p}3, {p}4)",
                        fid, forum["name"], forum.get("description", ""),
                        _parse_dt(forum.get("created_at")).isoformat()
                    )
                else:
                    await self.backend.execute(
                        f"INSERT INTO forums (id, name, description, created_at) VALUES ({p}1, {p}2, {p}3, {p}4) ON CONFLICT DO NOTHING",
                        fid, forum["name"], forum.get("description", ""),
                        _parse_dt(forum.get("created_at"))
                    )
        else:
            for forum in DEFAULT_FORUMS:
                if self._mode == "sqlite":
                    await self.backend.execute(
                        f"INSERT OR IGNORE INTO forums (id, name, description, created_at) VALUES ({p}1, {p}2, {p}3, {p}4)",
                        forum["id"], forum["name"], forum["description"], now.isoformat()
                    )
                else:
                    await self.backend.execute(
                        f"INSERT INTO forums (id, name, description, created_at) VALUES ({p}1, {p}2, {p}3, {p}4) ON CONFLICT DO NOTHING",
                        forum["id"], forum["name"], forum["description"], now
                    )

        for tid, topic in topics_data.items():
            closed = 1 if topic.get("closed") else 0
            if self._mode == "sqlite":
                await self.backend.execute(
                    f"INSERT OR IGNORE INTO topics (id, forum_id, title, author, closed, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5, {p}6)",
                    tid, topic["forum_id"], topic["title"], topic["author"],
                    closed, _parse_dt(topic.get("created_at")).isoformat()
                )
            else:
                await self.backend.execute(
                    f"INSERT INTO topics (id, forum_id, title, author, closed, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5, {p}6) ON CONFLICT DO NOTHING",
                    tid, topic["forum_id"], topic["title"], topic["author"],
                    topic.get("closed", False), _parse_dt(topic.get("created_at"))
                )

        for pid, post in posts_data.items():
            if self._mode == "sqlite":
                await self.backend.execute(
                    f"INSERT OR IGNORE INTO posts (id, topic_id, author, content, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5)",
                    pid, post["topic_id"], post["author"], post["content"],
                    _parse_dt(post.get("created_at")).isoformat()
                )
            else:
                await self.backend.execute(
                    f"INSERT INTO posts (id, topic_id, author, content, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5) ON CONFLICT DO NOTHING",
                    pid, post["topic_id"], post["author"], post["content"],
                    _parse_dt(post.get("created_at"))
                )

        if self._mode == "sqlite":
            await self.backend.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('motd', ?)",
                "Welcome to Chatwisp!"
            )
        else:
            await self.backend.execute(
                "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                "motd", "Welcome to Chatwisp!"
            )

        print("Database seeded from JSON files")

    @staticmethod
    def _hash(password):
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def _is_sha256_hash(stored):
        return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)

    @staticmethod
    def _parse_duration(text):
        if not text:
            return None
        text = text.strip().lower()
        total = 0
        import re
        parts = re.findall(r'(\d+)\s*(d|day|days|h|hr|hour|hours|m|min|minute|minutes)', text)
        for num, unit in parts:
            n = int(num)
            if unit.startswith('d'):
                total += n * 86400
            elif unit.startswith('h'):
                total += n * 3600
            elif unit.startswith('m'):
                total += n * 60
        return total if total > 0 else None

    async def get_user(self, username):
        p = self.backend.placeholder()
        row = await self.backend.fetchrow(f"SELECT * FROM users WHERE username = {p}1", username)
        row = self.backend.row_to_dict(row)
        if row:
            created = row.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            return {
                "username": row["username"],
                "password": row["password_hash"],
                "is_admin": bool(row["is_admin"]) if self._mode == "sqlite" else row["is_admin"],
                "super_admin": bool(row["super_admin"]) if self._mode == "sqlite" else row["super_admin"],
                "banned": bool(row["banned"]) if self._mode == "sqlite" else row["banned"],
                "ban_reason": row["ban_reason"],
                "ban_duration": row["ban_duration"],
                "ban_until": row.get("ban_until"),
                "is_bot": bool(row.get("is_bot", False)) if self._mode == "sqlite" else row.get("is_bot", False),
                "signature": row.get("signature") or "",
                "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            }
        return None

    async def create_user(self, username, password, is_admin=False, super_admin=False):
        p = self.backend.placeholder()
        try:
            await self.backend.execute(
                f"INSERT INTO users (username, password_hash, is_admin, super_admin) VALUES ({p}1, {p}2, {p}3, {p}4)",
                username, self._hash(password), is_admin, super_admin
            )
            return True
        except Exception:
            return False

    async def set_signature(self, username, signature):
        p = self.backend.placeholder()
        await self.backend.execute(
            f"UPDATE users SET signature = {p}1 WHERE username = {p}2",
            signature, username
        )

    async def get_music_prefs(self, username):
        p = self.backend.placeholder()
        row = await self.backend.fetchrow(f"SELECT music_prefs FROM users WHERE username = {p}1", username)
        row = self.backend.row_to_dict(row)
        if row and row["music_prefs"]:
            return json.loads(row["music_prefs"])
        return {}

    async def set_music_prefs(self, username, prefs):
        p = self.backend.placeholder()
        await self.backend.execute(
            f"UPDATE users SET music_prefs = {p}1 WHERE username = {p}2",
            json.dumps(prefs), username
        )

    async def verify_password(self, username, password):
        user = await self.get_user(username)
        if not user:
            return False
        stored = user["password"]
        if self._is_sha256_hash(stored):
            if hashlib.sha256(password.encode()).hexdigest() == stored:
                new_hash = Database._hash(password)
                pp = self.backend.placeholder()
                await self.backend.execute(
                    f"UPDATE users SET password_hash = {pp}1 WHERE username = {pp}2",
                    new_hash, username
                )
                return True
            return False
        return bcrypt.checkpw(password.encode(), stored.encode())

    async def has_admin(self):
        count = await self.backend.fetchval("SELECT COUNT(*) FROM users WHERE is_admin = 1" if self._mode == "sqlite" else "SELECT COUNT(*) FROM users WHERE is_admin = TRUE")
        return count and count > 0

    async def get_forums(self):
        rows = await self.backend.fetch("SELECT * FROM forums ORDER BY created_at ASC")
        return [{
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
        } for r in self.backend.rows_to_list(rows)]

    async def get_forum(self, forum_id):
        p = self.backend.placeholder()
        row = await self.backend.fetchrow(f"SELECT * FROM forums WHERE id = {p}1", forum_id)
        row = self.backend.row_to_dict(row)
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
        p = self.backend.placeholder()
        while True:
            existing = await self.backend.fetchval(f"SELECT id FROM forums WHERE id = {p}1", fid)
            if not existing:
                break
            fid = f"{base}_{counter}"
            counter += 1
        await self.backend.execute(
            f"INSERT INTO forums (id, name, description) VALUES ({p}1, {p}2, {p}3)",
            fid, name, description
        )
        return {"id": fid, "name": name, "description": description}

    async def get_topics(self, forum_id):
        p = self.backend.placeholder()
        rows = await self.backend.fetch(f"""
            SELECT t.*, (SELECT COUNT(*) FROM posts WHERE topic_id = t.id) as post_count
            FROM topics t WHERE t.forum_id = {p}1 ORDER BY t.created_at DESC
        """, forum_id)
        result = []
        for row in self.backend.rows_to_list(rows):
            created = row.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            result.append({
                "id": str(row["id"]),
                "title": row["title"],
                "author": row["author"],
                "closed": bool(row["closed"]) if self._mode == "sqlite" else row["closed"],
                "admin_only": bool(row.get("admin_only", False)) if self._mode == "sqlite" else row.get("admin_only", False),
                "slug": row.get("slug"),
                "post_count": row["post_count"],
                "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            })
        return result

    async def get_topic(self, topic_id):
        p = self.backend.placeholder()
        row = await self.backend.fetchrow(f"SELECT * FROM topics WHERE id = {p}1", topic_id)
        row = self.backend.row_to_dict(row)
        if row:
            created = row.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            return {
                "id": str(row["id"]),
                "forum_id": row["forum_id"],
                "title": row["title"],
                "author": row["author"],
                "closed": bool(row["closed"]) if self._mode == "sqlite" else row["closed"],
                "admin_only": bool(row.get("admin_only", False)) if self._mode == "sqlite" else row.get("admin_only", False),
                "slug": row.get("slug"),
                "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            }
        return None

    async def get_topic_by_slug(self, slug):
        p = self.backend.placeholder()
        row = await self.backend.fetchrow(f"SELECT * FROM topics WHERE slug = {p}1", slug)
        row = self.backend.row_to_dict(row)
        if row:
            created = row.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            return {
                "id": str(row["id"]),
                "forum_id": row["forum_id"],
                "title": row["title"],
                "author": row["author"],
                "closed": bool(row["closed"]) if self._mode == "sqlite" else row["closed"],
                "admin_only": bool(row.get("admin_only", False)) if self._mode == "sqlite" else row.get("admin_only", False),
                "slug": row.get("slug"),
                "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            }
        return None

    async def create_topic(self, forum_id, title, author, admin_only=False):
        tid = str(uuid.uuid4())
        slug = secrets.token_hex(12)
        p = self.backend.placeholder()
        await self.backend.execute(
            f"INSERT INTO topics (id, forum_id, title, author, admin_only, slug) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5, {p}6)",
            tid, forum_id, title, author, admin_only, slug
        )
        return {
            "id": tid,
            "forum_id": forum_id,
            "title": title,
            "author": author,
            "closed": False,
            "admin_only": admin_only,
            "slug": slug,
            "created_at": datetime.now().isoformat(),
        }

    async def close_topic(self, topic_id):
        p = self.backend.placeholder()
        result = await self.backend.execute(f"UPDATE topics SET closed = 1 WHERE id = {p}1" if self._mode == "sqlite" else f"UPDATE topics SET closed = TRUE WHERE id = {p}1", topic_id)
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")

    async def reopen_topic(self, topic_id):
        p = self.backend.placeholder()
        result = await self.backend.execute(f"UPDATE topics SET closed = 0 WHERE id = {p}1" if self._mode == "sqlite" else f"UPDATE topics SET closed = FALSE WHERE id = {p}1", topic_id)
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")

    async def get_posts(self, topic_id):
        p = self.backend.placeholder()
        rows = await self.backend.fetch(f"""
            SELECT p.*, u.signature
            FROM posts p
            JOIN users u ON p.author = u.username
            WHERE p.topic_id = {p}1
            ORDER BY p.created_at ASC
        """, topic_id)
        result = []
        for row in self.backend.rows_to_list(rows):
            created = row.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            result.append({
                "id": str(row["id"]),
                "topic_id": str(row["topic_id"]),
                "author": row["author"],
                "content": row["content"],
                "signature": row.get("signature") or row.get("signature", "") or "",
                "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            })
        return result

    async def create_post(self, topic_id, author, content):
        topic = await self.get_topic(topic_id)
        if not topic:
            return None
        if topic["closed"]:
            return None
        pid = str(uuid.uuid4())
        p = self.backend.placeholder()
        await self.backend.execute(
            f"INSERT INTO posts (id, topic_id, author, content) VALUES ({p}1, {p}2, {p}3, {p}4)",
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
        rows = await self.backend.fetch(
            "SELECT username, is_admin, super_admin, banned, ban_reason FROM users WHERE is_bot = 0 ORDER BY username ASC" if self._mode == "sqlite"
            else "SELECT username, is_admin, super_admin, banned, ban_reason FROM users WHERE is_bot = FALSE ORDER BY username ASC"
        )
        result = []
        for row in self.backend.rows_to_list(rows):
            result.append({
                "username": row["username"],
                "is_admin": bool(row["is_admin"]) if self._mode == "sqlite" else row["is_admin"],
                "super_admin": bool(row["super_admin"]) if self._mode == "sqlite" else row["super_admin"],
                "banned": bool(row["banned"]) if self._mode == "sqlite" else row["banned"],
                "ban_reason": row["ban_reason"],
            })
        return result

    async def get_all_usernames(self):
        rows = await self.backend.fetch(
            "SELECT username FROM users WHERE is_bot = 0 ORDER BY username" if self._mode == "sqlite"
            else "SELECT username FROM users WHERE is_bot = FALSE ORDER BY username"
        )
        return [r["username"] for r in self.backend.rows_to_list(rows)]

    async def ban_user(self, username, reason=None, duration=None, ban_until=None):
        p = self.backend.placeholder()
        result = await self.backend.execute(
            f"UPDATE users SET banned = 1, ban_reason = {p}2, ban_duration = {p}3, ban_until = {p}4 WHERE username = {p}1" if self._mode == "sqlite"
            else f"UPDATE users SET banned = TRUE, ban_reason = {p}2, ban_duration = {p}3, ban_until = {p}4 WHERE username = {p}1",
            username, reason, duration, ban_until
        )
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")

    async def unban_user(self, username):
        p = self.backend.placeholder()
        result = await self.backend.execute(
            f"UPDATE users SET banned = 0, ban_reason = NULL, ban_duration = NULL, ban_until = NULL WHERE username = {p}1" if self._mode == "sqlite"
            else f"UPDATE users SET banned = FALSE, ban_reason = NULL, ban_duration = NULL, ban_until = NULL WHERE username = {p}1",
            username
        )
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")

    async def delete_user(self, username):
        p = self.backend.placeholder()
        result = await self.backend.execute(f"DELETE FROM users WHERE username = {p}1", username)
        return self.backend.status_matches(result, "DELETE") and not self.backend.status_matches(result, "DELETE 0")

    async def promote_to_admin(self, username):
        p = self.backend.placeholder()
        result = await self.backend.execute(
            f"UPDATE users SET is_admin = 1 WHERE username = {p}1 AND is_admin = 0" if self._mode == "sqlite"
            else f"UPDATE users SET is_admin = TRUE WHERE username = {p}1 AND is_admin = FALSE",
            username
        )
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")

    async def demote_from_admin(self, username):
        p = self.backend.placeholder()
        result = await self.backend.execute(
            f"UPDATE users SET is_admin = 0 WHERE username = {p}1 AND is_admin = 1 AND super_admin = 0" if self._mode == "sqlite"
            else f"UPDATE users SET is_admin = FALSE WHERE username = {p}1 AND is_admin = TRUE AND super_admin = FALSE",
            username
        )
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")

    async def get_motd(self):
        row = await self.backend.fetchrow("SELECT value FROM settings WHERE key = 'motd'")
        row = self.backend.row_to_dict(row)
        if row:
            return row["value"]
        return "Welcome to Chatwisp!"

    async def set_motd(self, motd):
        p = self.backend.placeholder()
        if self._mode == "sqlite":
            await self.backend.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('motd', ?)",
                motd
            )
        else:
            await self.backend.execute(
                f"INSERT INTO settings (key, value) VALUES ('motd', {p}1) ON CONFLICT (key) DO UPDATE SET value = {p}1",
                motd
            )

    async def search_users(self, query):
        p = self.backend.placeholder()
        rows = await self.backend.fetch(
            f"SELECT username FROM users WHERE username LIKE {p}1 AND is_bot = 0 ORDER BY username ASC LIMIT 20" if self._mode == "sqlite"
            else f"SELECT username FROM users WHERE username ILIKE {p}1 AND is_bot = FALSE ORDER BY username ASC LIMIT 20",
            f"%{query}%"
        )
        return [r["username"] for r in self.backend.rows_to_list(rows)]

    async def send_dm(self, sender, recipient, content):
        mid = str(uuid.uuid4())
        now = datetime.now()
        p = self.backend.placeholder()
        await self.backend.execute(
            f"INSERT INTO dms (id, sender, recipient, content, created_at) VALUES ({p}1, {p}2, {p}3, {p}4, {p}5)",
            mid, sender, recipient, content, now.isoformat() if self._mode == "sqlite" else now
        )
        return {"id": mid, "sender": sender, "recipient": recipient, "content": content, "created_at": now.isoformat()}

    async def get_dm_conversation(self, user_a, user_b, limit=50):
        p = self.backend.placeholder()
        rows = await self.backend.fetch(
            f"""SELECT id, sender, recipient, content, created_at FROM dms
               WHERE (sender = {p}1 AND recipient = {p}2) OR (sender = {p}2 AND recipient = {p}1)
               ORDER BY created_at DESC LIMIT {p}3""",
            user_a, user_b, limit
        )
        result = []
        for r in reversed(self.backend.rows_to_list(rows)):
            created = r.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            result.append({
                "id": str(r["id"]),
                "sender": r["sender"],
                "recipient": r["recipient"],
                "content": r["content"],
                "created_at": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            })
        return result

    async def get_dm_contacts(self, username):
        p = self.backend.placeholder()
        if self._mode == "sqlite":
            rows = await self.backend.fetch(f"""
                SELECT sender, recipient, content, created_at, read
                FROM (
                    SELECT sender, recipient, content, created_at, read,
                        ROW_NUMBER() OVER (
                            PARTITION BY
                                CASE WHEN sender < recipient THEN sender ELSE recipient END,
                                CASE WHEN sender > recipient THEN sender ELSE recipient END
                            ORDER BY created_at DESC
                        ) as rn
                    FROM dms
                    WHERE sender = {p}1 OR recipient = {p}1
                ) WHERE rn = 1
                ORDER BY created_at DESC
            """, username)
        else:
            rows = await self.backend.fetch(
                f"""SELECT DISTINCT ON (pair) pair, sender, recipient, content, created_at, read FROM (
                     SELECT
                       GREATEST(sender, recipient) || ':' || LEAST(sender, recipient) AS pair,
                       sender, recipient, content, created_at, read
                     FROM dms
                     WHERE sender = {p}1 OR recipient = {p}1
                     ORDER BY pair, created_at DESC
                   ) sub ORDER BY pair, created_at DESC""",
                username
            )
        contacts = []
        for r in self.backend.rows_to_list(rows):
            other = r["recipient"] if r["sender"] == username else r["sender"]
            created = r.get("created_at")
            if created and isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except (ValueError, TypeError):
                    pass
            contacts.append({
                "username": other,
                "last_message": r["content"],
                "last_time": created.isoformat() if hasattr(created, 'isoformat') else str(created) if created else None,
            })
        return contacts

    async def mark_dms_read(self, recipient, sender):
        p = self.backend.placeholder()
        await self.backend.execute(
            f"UPDATE dms SET read = 1 WHERE recipient = {p}1 AND sender = {p}2 AND read = 0" if self._mode == "sqlite"
            else f"UPDATE dms SET read = TRUE WHERE recipient = {p}1 AND sender = {p}2 AND read = FALSE",
            recipient, sender
        )

    async def get_total_unread(self, username):
        p = self.backend.placeholder()
        return await self.backend.fetchval(
            f"SELECT COUNT(*) FROM dms WHERE recipient = {p}1 AND read = 0" if self._mode == "sqlite"
            else f"SELECT COUNT(*) FROM dms WHERE recipient = {p}1 AND read = FALSE",
            username
        )

    async def delete_post(self, post_id):
        p = self.backend.placeholder()
        result = await self.backend.execute(f"DELETE FROM posts WHERE id = {p}1", post_id)
        return self.backend.status_matches(result, "DELETE") and not self.backend.status_matches(result, "DELETE 0")

    async def delete_topic(self, topic_id):
        p = self.backend.placeholder()
        result = await self.backend.execute(f"DELETE FROM topics WHERE id = {p}1", topic_id)
        return self.backend.status_matches(result, "DELETE") and not self.backend.status_matches(result, "DELETE 0")

    async def create_voice_channel(self, forum_id, name):
        cid = str(uuid.uuid4())
        p = self.backend.placeholder()
        now = datetime.now(timezone.utc)
        await self.backend.execute(
            f"INSERT INTO voice_channels (id, forum_id, name, created_at) VALUES ({p}1, {p}2, {p}3, {p}4)",
            cid, forum_id, name, now if self._mode == "postgres" else now.isoformat()
        )
        return {"id": cid, "forum_id": forum_id, "name": name, "created_at": now.isoformat()}

    async def get_voice_channels(self, forum_id):
        p = self.backend.placeholder()
        rows = await self.backend.fetch(
            f"SELECT id, forum_id, name, created_at FROM voice_channels WHERE forum_id = {p}1 ORDER BY created_at ASC",
            forum_id
        )
        return [dict(r) for r in self.backend.rows_to_list(rows)]

    async def delete_voice_channel(self, channel_id):
        p = self.backend.placeholder()
        result = await self.backend.execute(f"DELETE FROM voice_channels WHERE id = {p}1", channel_id)
        return self.backend.status_matches(result, "DELETE") and not self.backend.status_matches(result, "DELETE 0")

    async def update_password(self, username, new_password):
        p = self.backend.placeholder()
        await self.backend.execute(
            f"UPDATE users SET password_hash = {p}1 WHERE username = {p}2",
            self._hash(new_password), username
        )
        return True

    async def set_topic_admin_only(self, topic_id, value):
        p = self.backend.placeholder()
        result = await self.backend.execute(
            f"UPDATE topics SET admin_only = {p}1 WHERE id = {p}2", value, topic_id
        )
        return self.backend.status_matches(result, "UPDATE") and not self.backend.status_matches(result, "UPDATE 0")


class ChatServer:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.storage = Database()
        self.clients = {}
        self._login_attempts = {}
        self.voice_members = {}
        self.voice_states = {}
        self._voice_signal_counts = {}

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

    def _is_rate_limited(self, key):
        now = time.time()
        attempts = self._login_attempts.get(key, [])
        attempts = [t for t in attempts if now - t < 60]
        self._login_attempts[key] = attempts
        return len(attempts) >= 10

    def _record_attempt(self, key):
        now = time.time()
        self._login_attempts.setdefault(key, [])
        self._login_attempts[key] = [t for t in self._login_attempts[key] if now - t < 60]
        self._login_attempts[key].append(now)

    async def _reject_outdated_client(self, websocket, error_type, client_version):
        if not client_version:
            await self.send(websocket, {"type": error_type, "message": "Your client is out of date. Please download the latest version from " + DOWNLOAD_URL})
            return True
        try:
            cv = tuple(int(x) for x in client_version.split("."))
            sv = tuple(int(x) for x in VERSION.split("."))
            if cv[0] != sv[0] or client_version < MINIMUM_CLIENT_VERSION:
                await self.send(websocket, {"type": error_type, "message": f"Your client is out of date. Please download the latest version from {DOWNLOAD_URL}"})
                return True
        except (ValueError, IndexError):
            await self.send(websocket, {"type": error_type, "message": "Your client is out of date. Please download the latest version from " + DOWNLOAD_URL})
            return True
        return False

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
            "ping": self.handle_ping,
            "server_info": self.handle_server_info,
            "bot_send_dm": self.handle_bot_send_dm,
            "bot_broadcast": self.handle_bot_broadcast,
            "bot_create_post": self.handle_bot_create_post,
            "bot_create_topic": self.handle_bot_create_topic,
            "set_signature": self.handle_set_signature,
            "get_signature": self.handle_get_signature,
            "get_music_prefs": self.handle_get_music_prefs,
            "set_music_prefs": self.handle_set_music_prefs,
            "resolve_topic_link": self.handle_resolve_topic_link,
            "voice_create": self.handle_voice_create,
            "voice_channels": self.handle_voice_channels,
            "voice_join": self.handle_voice_join,
            "voice_leave": self.handle_voice_leave,
            "voice_signal": self.handle_voice_signal,
            "voice_mute": self.handle_voice_mute,
            "voice_deafen": self.handle_voice_deafen,
        }

        handler = handlers.get(msg_type)
        if handler:
            try:
                await handler(websocket, data)
            except Exception as e:
                import traceback
                traceback.print_exc()
                await self.send(websocket, {"type": "error", "message": f"Server error: {e}"})
        else:
            await self.send(websocket, {"type": "error", "message": "Unknown message type"})

    async def handle_login(self, websocket, data):
        username = data.get("username", "").strip()
        password = data.get("password", "")
        client_version = data.get("client_version", "")
        if not username or not password:
            await self.send(websocket, {"type": "login_error", "message": "Username and password required"})
            return
        ip = websocket.remote_address[0] if hasattr(websocket, "remote_address") else "unknown"
        if self._is_rate_limited(f"login:{ip}"):
            await self.send(websocket, {"type": "login_error", "message": "Too many login attempts. Please try again later."})
            return
        self._record_attempt(f"login:{ip}")
        if await self._reject_outdated_client(websocket, "login_error", client_version):
            return
        user = await self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "login_error", "message": "Invalid username or password"})
            return
        if user.get("is_bot"):
            await self.send(websocket, {"type": "login_error", "message": "The Chatwisp Official Account cannot be logged into."})
            return
        now = datetime.now(timezone.utc)
        if user.get("banned"):
            ban_until = user.get("ban_until")
            reason = user.get("ban_reason") or "No reason given"
            if ban_until and ban_until < now:
                await self.storage.unban_user(username)
            elif ban_until:
                remaining = ban_until - now
                days = remaining.days
                hours = remaining.seconds // 3600
                mins = (remaining.seconds % 3600) // 60
                parts = []
                if days: parts.append(f"{days}d")
                parts.append(f"{hours}h {mins}m")
                time_str = " ".join(parts)
                await self.send(websocket, {"type": "login_error", "message": f"You are banned. Remaining time: {time_str}. Reason: {reason}"})
                return
            else:
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
        client_version = data.get("client_version", "")
        if not username or not password:
            await self.send(websocket, {"type": "register_error", "message": "Username and password required"})
            return
        ip = websocket.remote_address[0] if hasattr(websocket, "remote_address") else "unknown"
        if self._is_rate_limited(f"register:{ip}"):
            await self.send(websocket, {"type": "register_error", "message": "Too many registration attempts. Please try again later."})
            return
        self._record_attempt(f"register:{ip}")
        if await self._reject_outdated_client(websocket, "register_error", client_version):
            return
        if len(username) < 3:
            await self.send(websocket, {"type": "register_error", "message": "Username must be at least 3 characters"})
            return
        if len(password) < 8:
            await self.send(websocket, {"type": "register_error", "message": "Password must be at least 8 characters"})
            return
        if await self.storage.get_user(username):
            await self.send(websocket, {"type": "register_error", "message": "Username already exists"})
            return
        await self.storage.create_user(username, password, is_admin=False)
        await self.send(websocket, {"type": "register_success"})
        welcome = ("Welcome to Chatwisp!\n\n"
                   "Chatwisp is a community forum where you can:\n"
                   "- Browse and post in topic-based forums\n"
                   "- Send private messages to other users\n"
                   "- Customize your experience\n\n"
                   "To get started, select a forum from the main menu and join the conversation!")
        await self.storage.send_dm(BOT_USERNAME, username, welcome)
        for ws, info in list(self.clients.items()):
            if info["username"] == username:
                await self.send(ws, {"type": "dm_received", "dm": {"sender": BOT_USERNAME, "content": welcome}})

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
        if username == BOT_USERNAME:
            await self.send(websocket, {"type": "error", "message": "Cannot ban the official account"})
            return
        target = await self.storage.get_user(username)
        if not target:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        if target.get("is_admin") and not self.clients[websocket].get("super_admin", False):
            await self.send(websocket, {"type": "error", "message": "Only super admins can ban other admins"})
            return
        reason = data.get("reason")
        duration = data.get("duration")
        ban_until = None
        if duration:
            seconds = Database._parse_duration(duration)
            if seconds:
                ban_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        if await self.storage.ban_user(username, reason, duration, ban_until):
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
        if user.get("is_bot"):
            await self.send(websocket, {"type": "error", "message": "The Chatwisp Official Account cannot receive messages."})
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
        if len(new_password) < 8:
            await self.send(websocket, {"type": "error", "message": "Password must be at least 8 characters"})
            return
        await self.storage.update_password(username, new_password)
        await self.send(websocket, {"type": "password_reset", "username": username, "message": f"Password for {username} has been reset"})

    async def handle_bot_send_dm(self, websocket, data):
        if not self.require_admin(websocket):
            return await self.send(websocket, {"type": "error", "message": "Admin access required"})
        recipient = data.get("recipient", "").strip()
        content = data.get("content", "").strip()
        if not recipient or not content:
            return await self.send(websocket, {"type": "error", "message": "Recipient and content required"})
        user = await self.storage.get_user(recipient)
        if not user:
            return await self.send(websocket, {"type": "error", "message": "User not found"})
        if user.get("is_bot"):
            return await self.send(websocket, {"type": "error", "message": "Cannot send DMs to the bot through this command"})
        dm = await self.storage.send_dm(BOT_USERNAME, recipient, content)
        await self.send(websocket, {"type": "bot_dm_sent", "dm": dm})
        for ws, info in list(self.clients.items()):
            if info["username"] == recipient:
                await self.send(ws, {"type": "dm_received", "dm": dm})

    async def handle_bot_broadcast(self, websocket, data):
        if not self.require_admin(websocket):
            return await self.send(websocket, {"type": "error", "message": "Admin access required"})
        content = data.get("content", "").strip()
        if not content:
            return await self.send(websocket, {"type": "error", "message": "Content required"})
        all_users = await self.storage.get_all_usernames()
        sent_count = 0
        for recipient in all_users:
            dm = await self.storage.send_dm(BOT_USERNAME, recipient, content)
            sent_count += 1
            for ws, info in list(self.clients.items()):
                if info["username"] == recipient:
                    await self.send(ws, {"type": "dm_received", "dm": dm})
        await self.send(websocket, {"type": "bot_broadcast_complete", "message": f"Broadcast sent to {sent_count} users."})

    async def handle_bot_create_post(self, websocket, data):
        if not self.require_admin(websocket):
            return await self.send(websocket, {"type": "error", "message": "Admin access required"})
        topic_id = data.get("topic_id", "").strip()
        content = data.get("content", "").strip()
        if not topic_id or not content:
            return await self.send(websocket, {"type": "error", "message": "topic_id and content required"})
        post = await self.storage.create_post(topic_id, BOT_USERNAME, content)
        await self.send(websocket, {"type": "bot_post_created", "post": post})

    async def handle_bot_create_topic(self, websocket, data):
        if not self.require_admin(websocket):
            return await self.send(websocket, {"type": "error", "message": "Admin access required"})
        forum_id = data.get("forum_id", "").strip()
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        if not forum_id or not title:
            return await self.send(websocket, {"type": "error", "message": "forum_id and title required"})
        topic = await self.storage.create_topic(forum_id, title, BOT_USERNAME)
        if content:
            await self.storage.create_post(topic["id"], BOT_USERNAME, content)
        await self.send(websocket, {"type": "bot_topic_created", "topic": topic})

    async def handle_set_signature(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        user = self.clients[websocket]
        signature = data.get("signature", "").strip()
        if len(signature) > 50:
            return await self.send(websocket, {"type": "error", "message": "Signature must be 50 characters or less"})
        await self.storage.set_signature(user["username"], signature)
        await self.send(websocket, {"type": "signature_updated", "message": "Signature updated"})

    async def handle_get_signature(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        user = self.clients[websocket]
        full_user = await self.storage.get_user(user["username"])
        sig = full_user.get("signature", "") if full_user else ""
        await self.send(websocket, {"type": "signature_data", "signature": sig})

    async def handle_get_music_prefs(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        user = self.clients[websocket]
        prefs = await self.storage.get_music_prefs(user["username"])
        await self.send(websocket, {"type": "music_prefs_data", "prefs": prefs})

    async def handle_set_music_prefs(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        user = self.clients[websocket]
        prefs = data.get("prefs", {})
        if not isinstance(prefs, dict):
            return await self.send(websocket, {"type": "error", "message": "Invalid prefs format"})
        valid_keys = {"main_menu", "forum", "topic"}
        for k in prefs:
            if k not in valid_keys:
                return await self.send(websocket, {"type": "error", "message": f"Invalid key: {k}"})
            if prefs[k] not in ("", *AVAILABLE_SONGS):
                return await self.send(websocket, {"type": "error", "message": f"Invalid song: {prefs[k]}"})
        await self.storage.set_music_prefs(user["username"], prefs)
        await self.send(websocket, {"type": "music_prefs_updated", "message": "Music preferences saved"})

    async def handle_resolve_topic_link(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        slug = data.get("slug", "").strip()
        if not slug:
            return await self.send(websocket, {"type": "error", "message": "Slug required"})
        topic = await self.storage.get_topic_by_slug(slug)
        if not topic:
            return await self.send(websocket, {"type": "error", "message": "Topic not found"})
        await self.send(websocket, {
            "type": "topic_link_resolved",
            "forum_id": topic["forum_id"],
            "topic_id": topic["id"],
            "title": topic["title"],
        })

    async def handle_ping(self, websocket, data):
        client_time = data.get("client_time", 0)
        await self.send(websocket, {"type": "pong", "client_time": client_time, "server_time": time.time()})

    async def handle_server_info(self, websocket, data):
        uptime = int(time.time() - SERVER_START_TIME)
        await self.send(websocket, {"type": "server_info", "uptime": uptime})

    async def _broadcast_voice(self, channel_id, msg, exclude=None):
        members = self.voice_members.get(channel_id, {})
        for username, ws in list(members.items()):
            if ws is exclude:
                continue
            if ws in self.clients:
                await self.send(ws, msg)

    def _leave_voice(self, websocket):
        state = self.voice_states.pop(websocket, None)
        if state and state.get("channel_id"):
            cid = state["channel_id"]
            username = state.get("username")
            members = self.voice_members.get(cid)
            if members:
                members.pop(username, None)
                if not members:
                    self.voice_members.pop(cid, None)
            if username:
                asyncio.ensure_future(self._broadcast_voice(cid, {
                    "type": "voice_user_left",
                    "channel_id": cid,
                    "username": username,
                }))

    async def handle_voice_create(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        forum_id = data.get("forum_id", "").strip()
        name = data.get("name", "").strip()
        if not forum_id or not name:
            return await self.send(websocket, {"type": "error", "message": "forum_id and name required"})
        try:
            channel = await self.storage.create_voice_channel(forum_id, name)
        except Exception as e:
            return await self.send(websocket, {"type": "error", "message": f"Failed to create voice channel: {e}"})
        await self.send(websocket, {"type": "voice_channel_created", "channel": channel})

    async def handle_voice_channels(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        forum_id = data.get("forum_id", "").strip()
        if not forum_id:
            return await self.send(websocket, {"type": "error", "message": "forum_id required"})
        channels = await self.storage.get_voice_channels(forum_id)
        enriched = []
        for ch in channels:
            member_count = len(self.voice_members.get(ch["id"], {}))
            enriched.append({**ch, "member_count": member_count})
        await self.send(websocket, {"type": "voice_channels_list", "forum_id": forum_id, "channels": enriched})

    async def handle_voice_join(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        username = self.clients[websocket]["username"]
        self._leave_voice(websocket)
        channel_id = data.get("channel_id", "").strip()
        if not channel_id:
            return await self.send(websocket, {"type": "error", "message": "channel_id required"})
        p = self.storage.backend.placeholder()
        row = await self.storage.backend.fetchrow(
            f"SELECT id, forum_id, name, created_at FROM voice_channels WHERE id = {p}1", channel_id
        )
        if not row:
            return await self.send(websocket, {"type": "error", "message": "Channel not found"})
        channel = dict(row)
        if channel_id not in self.voice_members:
            self.voice_members[channel_id] = {}
        self.voice_members[channel_id][username] = websocket
        self.voice_states[websocket] = {"channel_id": channel_id, "username": username, "muted": False, "deafened": False}
        members = list(self.voice_members[channel_id].keys())
        await self.send(websocket, {"type": "voice_joined", "channel_id": channel_id, "members": members})
        await self._broadcast_voice(channel_id, {
            "type": "voice_user_joined",
            "channel_id": channel_id,
            "username": username,
            "members": members,
        }, exclude=websocket)

    async def handle_voice_leave(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        self._leave_voice(websocket)
        await self.send(websocket, {"type": "voice_left"})

    async def handle_voice_signal(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        now = time.time()
        timestamps = self._voice_signal_counts.get(websocket, [])
        timestamps = [t for t in timestamps if now - t < 1.0]
        if len(timestamps) >= 20:
            return
        timestamps.append(now)
        self._voice_signal_counts[websocket] = timestamps
        target = data.get("target", "").strip()
        signal_type = data.get("signal_type", "")
        payload = data.get("payload", {})
        channel_id = data.get("channel_id", "")
        if not target or not signal_type:
            return
        members = self.voice_members.get(channel_id, {})
        target_ws = members.get(target)
        if target_ws and target_ws in self.clients:
            await self.send(target_ws, {
                "type": "voice_signal",
                "from": self.clients[websocket]["username"],
                "signal_type": signal_type,
                "payload": payload,
                "channel_id": channel_id,
            })

    async def handle_voice_mute(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        state = self.voice_states.get(websocket)
        if not state:
            return await self.send(websocket, {"type": "error", "message": "Not in a voice channel"})
        muted = data.get("muted", not state["muted"])
        state["muted"] = muted
        channel_id = state["channel_id"]
        username = state["username"]
        await self._broadcast_voice(channel_id, {
            "type": "voice_user_muted" if muted else "voice_user_unmuted",
            "channel_id": channel_id,
            "username": username,
        }, exclude=websocket)

    async def handle_voice_deafen(self, websocket, data):
        if not self.require_auth(websocket):
            return await self.send(websocket, {"type": "error", "message": "Not authenticated"})
        state = self.voice_states.get(websocket)
        if not state:
            return await self.send(websocket, {"type": "error", "message": "Not in a voice channel"})
        deafened = data.get("deafened", not state["deafened"])
        state["deafened"] = deafened
        channel_id = state["channel_id"]
        username = state["username"]
        await self._broadcast_voice(channel_id, {
            "type": "voice_user_deafened" if deafened else "voice_user_undeafened",
            "channel_id": channel_id,
            "username": username,
        }, exclude=websocket)

    async def handler(self, websocket):
        try:
            async for raw in websocket:
                await self.handle_message(websocket, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.pop(websocket, None)
            self._leave_voice(websocket)

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
            if request.path == "/manifest.json" and MANIFEST_JSON is not None:
                return Response(200, "OK", Headers({"Content-Type": "application/manifest+json; charset=utf-8"}), MANIFEST_JSON.encode("utf-8"))
            if request.path == "/sw.js" and SW_JS is not None:
                return Response(200, "OK", Headers({"Content-Type": "application/javascript; charset=utf-8"}), SW_JS.encode("utf-8"))
            if request.path.startswith("/icons/") and request.path.count("/") == 2:
                filename = request.path.split("/")[-1]
                filepath = os.path.join(CLIENT_WEB_DIR, "icons", filename)
                if os.path.isfile(filepath):
                    with open(filepath, "rb") as f:
                        content_type = "image/png" if filename.endswith(".png") else "application/octet-stream"
                        return Response(200, "OK", Headers({"Content-Type": content_type}), f.read())
                return Response(404, "Not Found", Headers({"Content-Type": "text/plain; charset=utf-8"}), b"File not found\n")
            if request.path.startswith("/forums/") and INDEX_HTML is not None:
                return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), INDEX_HTML.encode("utf-8"))
            if request.path.startswith("/music/") and request.path.count("/") == 2:
                filename = request.path.split("/")[-1]
                filepath = os.path.join(MUSIC_DIR, filename)
                if os.path.isfile(filepath):
                    with open(filepath, "rb") as f:
                        return Response(200, "OK", Headers({"Content-Type": "audio/mpeg"}), f.read())
                return Response(404, "Not Found", Headers({"Content-Type": "text/plain; charset=utf-8"}), b"File not found\n")
            return Response(404, "Not Found", Headers({"Content-Type": "text/plain; charset=utf-8"}), b"Not Found\n")

        loop = asyncio.get_running_loop()

        try:
            async with websockets.serve(self.handler, self.host, self.port, process_request=health_check) as server:
                if hasattr(signal, "SIGTERM"):
                    try:
                        loop.add_signal_handler(signal.SIGTERM, server.close)
                    except NotImplementedError:
                        pass
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
