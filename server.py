#!/usr/bin/env python3
import asyncio
import json
import hashlib
import os
import uuid
import sys
from datetime import datetime

try:
    import websockets
except ImportError:
    print("Error: websockets library not found. Run: pip install websockets")
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

class Storage:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.users = self._load(USERS_FILE, {})
        self.forums = self._load(FORUMS_FILE, {})
        self.topics = self._load(TOPICS_FILE, {})
        self.posts = self._load(POSTS_FILE, {})

        if not self.forums:
            for forum in DEFAULT_FORUMS:
                self.forums[forum["id"]] = dict(forum)

    def _load(self, path, default):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return default

    def _save_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_all(self):
        self._save_json(USERS_FILE, self.users)
        self._save_json(FORUMS_FILE, self.forums)
        self._save_json(TOPICS_FILE, self.topics)
        self._save_json(POSTS_FILE, self.posts)

    @staticmethod
    def _hash(password):
        return hashlib.sha256(password.encode()).hexdigest()

    def get_user(self, username):
        return self.users.get(username)

    def create_user(self, username, password, is_admin=False):
        if username in self.users:
            return False
        self.users[username] = {
            "username": username,
            "password": self._hash(password),
            "is_admin": is_admin,
            "banned": False,
            "ban_reason": None,
            "created_at": datetime.now().isoformat(),
        }
        self.save_all()
        return True

    def verify_password(self, username, password):
        user = self.users.get(username)
        if not user:
            return False
        return user["password"] == self._hash(password)

    def has_admin(self):
        return any(u.get("is_admin") for u in self.users.values())

    def get_forums(self):
        return list(self.forums.values())

    def get_forum(self, forum_id):
        return self.forums.get(forum_id)

    def create_forum(self, name, description):
        fid = name.lower().replace(" ", "_").replace("/", "_")
        base = fid
        counter = 1
        while fid in self.forums:
            fid = f"{base}_{counter}"
            counter += 1
        self.forums[fid] = {
            "id": fid,
            "name": name,
            "description": description,
            "created_at": datetime.now().isoformat(),
        }
        self.save_all()
        return self.forums[fid]

    def get_topics(self, forum_id):
        return [t for t in self.topics.values() if t["forum_id"] == forum_id]

    def get_topic(self, topic_id):
        return self.topics.get(topic_id)

    def create_topic(self, forum_id, title, author):
        tid = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self.topics[tid] = {
            "id": tid,
            "forum_id": forum_id,
            "title": title,
            "author": author,
            "closed": False,
            "created_at": now,
        }
        self.save_all()
        return self.topics[tid]

    def close_topic(self, topic_id):
        topic = self.topics.get(topic_id)
        if topic:
            topic["closed"] = True
            self.save_all()
            return True
        return False

    def reopen_topic(self, topic_id):
        topic = self.topics.get(topic_id)
        if topic:
            topic["closed"] = False
            self.save_all()
            return True
        return False

    def get_posts(self, topic_id):
        return [p for p in self.posts.values() if p["topic_id"] == topic_id]

    def create_post(self, topic_id, author, content):
        topic = self.topics.get(topic_id)
        if not topic:
            return None
        if topic["closed"]:
            return None
        pid = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self.posts[pid] = {
            "id": pid,
            "topic_id": topic_id,
            "author": author,
            "content": content,
            "created_at": now,
        }
        self.save_all()
        return self.posts[pid]

    def get_all_users(self):
        result = []
        for u in self.users.values():
            result.append({
                "username": u["username"],
                "is_admin": u.get("is_admin", False),
                "banned": u.get("banned", False),
                "ban_reason": u.get("ban_reason"),
            })
        return result

    def ban_user(self, username, reason=None, duration=None):
        user = self.users.get(username)
        if not user:
            return False
        user["banned"] = True
        user["ban_reason"] = reason
        user["ban_duration"] = duration
        self.save_all()
        return True

    def unban_user(self, username):
        user = self.users.get(username)
        if not user:
            return False
        user["banned"] = False
        user["ban_reason"] = None
        user["ban_duration"] = None
        self.save_all()
        return True

    def delete_user(self, username):
        if username in self.users:
            del self.users[username]
            self.save_all()
            return True
        return False


class ChatServer:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.storage = Storage()
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
        user = self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "login_error", "message": "Invalid username or password"})
            return
        if user.get("banned"):
            reason = user.get("ban_reason") or "No reason given"
            await self.send(websocket, {"type": "login_error", "message": f"You are banned. Reason: {reason}"})
            return
        if not self.storage.verify_password(username, password):
            await self.send(websocket, {"type": "login_error", "message": "Invalid username or password"})
            return
        self.clients[websocket] = {"username": username, "is_admin": user.get("is_admin", False)}
        await self.send(websocket, {
            "type": "login_success",
            "username": username,
            "is_admin": user.get("is_admin", False),
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
        if self.storage.get_user(username):
            await self.send(websocket, {"type": "register_error", "message": "Username already exists"})
            return
        self.storage.create_user(username, password, is_admin=False)
        await self.send(websocket, {"type": "register_success"})

    async def handle_get_forums(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        forums = self.storage.get_forums()
        await self.send(websocket, {"type": "forums_list", "forums": forums})

    async def handle_get_topics(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        forum_id = data.get("forum_id")
        if not forum_id:
            await self.send(websocket, {"type": "error", "message": "forum_id required"})
            return
        topics = self.storage.get_topics(forum_id)
        result = []
        for t in topics:
            posts = self.storage.get_posts(t["id"])
            result.append({
                "id": t["id"],
                "title": t["title"],
                "author": t["author"],
                "closed": t["closed"],
                "post_count": len(posts),
                "created_at": t["created_at"],
            })
        await self.send(websocket, {"type": "topics_list", "forum_id": forum_id, "topics": result})

    async def handle_get_posts(self, websocket, data):
        if not self.require_auth(websocket):
            await self.send(websocket, {"type": "error", "message": "Not authenticated"})
            return
        topic_id = data.get("topic_id")
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        topic = self.storage.get_topic(topic_id)
        if not topic:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})
            return
        posts = self.storage.get_posts(topic_id)
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
        forum = self.storage.get_forum(forum_id)
        if not forum:
            await self.send(websocket, {"type": "error", "message": "Forum not found"})
            return
        user = self.clients[websocket]
        topic = self.storage.create_topic(forum_id, title, user["username"])
        if content:
            post = self.storage.create_post(topic["id"], user["username"], content)
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
        topic = self.storage.get_topic(topic_id)
        if not topic:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})
            return
        if topic["closed"]:
            await self.send(websocket, {"type": "error", "message": "Cannot post in a closed topic"})
            return
        user = self.clients[websocket]
        post = self.storage.create_post(topic_id, user["username"], content)
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
        forum = self.storage.create_forum(name, description)
        await self.send(websocket, {"type": "forum_created", "forum": forum})

    async def handle_close_topic(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        topic_id = data.get("topic_id")
        if not topic_id:
            await self.send(websocket, {"type": "error", "message": "topic_id required"})
            return
        if self.storage.close_topic(topic_id):
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
        if self.storage.reopen_topic(topic_id):
            await self.send(websocket, {"type": "topic_reopened", "topic_id": topic_id})
        else:
            await self.send(websocket, {"type": "error", "message": "Topic not found"})

    async def handle_get_users(self, websocket, data):
        if not self.require_admin(websocket):
            await self.send(websocket, {"type": "error", "message": "Admin access required"})
            return
        users = self.storage.get_all_users()
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
        if self.storage.ban_user(username, reason, duration):
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
        if self.storage.unban_user(username):
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
        user = self.storage.get_user(username)
        if not user:
            await self.send(websocket, {"type": "error", "message": "User not found"})
            return
        if user.get("is_admin") and not self.storage.has_admin():
            await self.send(websocket, {"type": "error", "message": "Cannot delete the last admin"})
            return
        for ws, info in list(self.clients.items()):
            if info["username"] == username:
                await self.send(ws, {"type": "error", "message": "Your account has been deleted"})
                await ws.close()
        self.storage.delete_user(username)
        await self.send(websocket, {"type": "user_deleted", "username": username, "message": f"User {username} has been deleted"})

    async def handle_create_dev_account(self, websocket, data):
        if self.storage.has_admin():
            await self.send(websocket, {"type": "error", "message": "A developer account already exists"})
            return
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if not username or not password:
            await self.send(websocket, {"type": "error", "message": "Username and password required"})
            return
        if self.storage.get_user(username):
            await self.send(websocket, {"type": "error", "message": "Username already exists"})
            return
        self.storage.create_user(username, password, is_admin=True)
        await self.send(websocket, {"type": "dev_account_created", "message": "Developer account created"})

    async def handler(self, websocket):
        try:
            async for raw in websocket:
                await self.handle_message(websocket, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.pop(websocket, None)

    async def start(self):
        print(f"Loading server data...")
        print(f"Server starting on ws://{self.host}:{self.port}")
        print(f"Admin accounts exist: {self.storage.has_admin()}")
        async with websockets.serve(self.handler, self.host, self.port):
            await asyncio.Future()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Witecanechat Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()

    server = ChatServer(host=args.host, port=args.port)
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nServer shutting down...")


if __name__ == "__main__":
    main()
