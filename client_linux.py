#!/usr/bin/env python3
"""
Chatwisp — Linux client (curses TUI).

A terminal user interface for the Chatwisp chat server. It speaks the exact
same WebSocket protocol as the Windows client (client_windows.py), so it is
fully interoperable: a Linux user can connect to the same servers and share
forums, topics, posts and direct messages with Windows and web users.

Requirements:
    pip install websockets        # the only third-party dependency

Usage:
    python3 client_linux.py

Run on any Linux terminal (works over SSH too). Screen reader friendly:
status announcements are spoken via espeak/spd-say when available, mirroring
the Windows client's NVDA/SAPI accessibility support.
"""

VERSION = "4.1.0"
DEFAULT_URI = "wss://chatwisp.onrender.com"
AUTH_SERVER_URL = "https://christmaschild-auth.onrender.com"

import json
import threading
import queue
import sys
import os
import time
import curses
import curses.textpad
import textwrap
import subprocess
import shutil
import random
import secrets
import urllib.request
import urllib.parse
import http.server
import webbrowser


# --------------------------------------------------------------------------- #
#  Optional third-party dependency (lazy import so the module loads without it)
# --------------------------------------------------------------------------- #
_ws_module = None


def _get_websockets():
    global _ws_module
    if _ws_module is None:
        import importlib
        _ws_module = importlib.import_module("websockets.sync.client")
    return _ws_module


def websockets_available():
    try:
        _get_websockets()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Text-to-speech (accessibility), Linux equivalent of the Windows NVDA/SAPI
# --------------------------------------------------------------------------- #
def _tts_speak(text):
    if not text:
        return
    for exe in ("spd-say", "espeak-ng", "espeak"):
        path = shutil.which(exe)
        if path:
            try:
                subprocess.Popen(
                    [path, text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                return
            except Exception:
                continue


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _safe_str(v):
    if v is None:
        return ""
    return str(v)


def _confirm(stdscr, prompt):
    """Yes/No confirm dialog. Returns True for yes."""
    stdscr.addstr(curses.LINES - 2, 0, " " * (curses.COLS - 1))
    stdscr.addstr(curses.LINES - 2, 0, prompt[: curses.COLS - 1], curses.A_BOLD)
    stdscr.clrtoeol()
    curses.echo()
    curses.curs_set(1)
    stdscr.nodelay(False)
    while True:
        try:
            ch = stdscr.getch(curses.LINES - 2, min(len(prompt), curses.COLS - 2))
        except curses.error:
            ch = -1
        curses.noecho()
        curses.curs_set(0)
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N"), 27, curses.KEY_CANCEL):
            return False
        if ch in (10, 13):
            return True
        # redraw prompt and wait again
        stdscr.addstr(curses.LINES - 2, 0, " " * (curses.COLS - 1))
        stdscr.addstr(curses.LINES - 2, 0, prompt[: curses.COLS - 1], curses.A_BOLD)
        stdscr.clrtoeol()


def _prompt(stdscr, label, initial="", secret=False, max_len=200):
    """Single-line text prompt. Returns entered string or None on Esc."""
    h, w = stdscr.getmaxyx()
    row = h - 2
    buf = list(initial)
    curses.curs_set(1)
    stdscr.nodelay(False)
    while True:
        stdscr.addstr(row, 0, " " * (w - 1))
        disp = "".join(buf)
        if secret:
            disp = "*" * len(disp)
        stdscr.addstr(row, 0, label[: w - len(disp) - 2] + " " + disp)
        stdscr.clrtoeol()
        col = min(len(label) + 1 + len(buf), w - 2)
        try:
            ch = stdscr.getch(row, col)
        except curses.error:
            ch = -1
        if ch in (10, 13):
            curses.curs_set(0)
            return "".join(buf)
        if ch in (27, curses.KEY_CANCEL):
            curses.curs_set(0)
            return None
        if ch in (curses.KEY_BACKSPACE, 8, 127):
            if buf:
                buf.pop()
        elif ch == curses.KEY_RESIZE:
            h, w = stdscr.getmaxyx()
        elif 32 <= ch <= 126 and len(buf) < max_len:
            buf.append(chr(ch))


# --------------------------------------------------------------------------- #
#  Application
# --------------------------------------------------------------------------- #
class ChatwispApp:
    def __init__(self, stdscr=None):
        # connection / auth state
        self.stdscr = stdscr
        self.ws = None
        self.connected = False
        self.running = True
        self.username = None
        self.is_admin = False
        self.is_super_admin = False
        self._server_uri = DEFAULT_URI
        self._saved_uri = None
        self._saved_username = None
        self._saved_password = None
        self._reconnecting = False
        self._last_ping_time = 0.0
        self._pending_ping_time = 0.0
        self._pending_server_info = False
        self._ccauth_token = None

        # queues: networking thread -> main thread
        self.recv_queue = queue.Queue()

        # data caches
        self.forums = []
        self.topics = []
        self.posts = []
        self.current_topic = None
        self.users = []
        self.dm_contacts = []
        self.dm_messages = []
        self.dm_contact = None
        self.signature = ""
        self.unread_count = 0
        self.motd = ""

        # navigation stacks (kept in the client so the dispatch can re-request)
        self.forum_id_stack = []
        self.topic_id_stack = []

        # UI state
        self.view_stack = []
        self.view = None
        self.status = "Welcome to Chatwisp"
        self.dirty = True
        self.input_buf = ""
        self.input_label = ""
        self.input_active = False
        self.scroll = 0
        self.cursor = 0  # selected display line within current view
        self._lines = []  # display lines for the current view
        self._line_meta = []  # parallel metadata (e.g. post index) per display line

    # ------------------------------------------------------------------ #
    #  Networking
    # ------------------------------------------------------------------ #
    def _send(self, msg):
        if not self.connected or not self.ws:
            self._set_status("Not connected")
            return
        try:
            self.ws.send(json.dumps(msg))
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    def _connect(self, uri, username, password, mode):
        """mode: 'login' or 'register'. Runs in a background thread."""
        try:
            ws_mod = _get_websockets()
            with ws_mod.connect(uri) as ws:
                self.ws = ws
                self.connected = True
                ws.send(json.dumps({
                    "type": mode, "username": username, "password": password,
                    "client_version": VERSION,
                }))
                response = json.loads(ws.recv())
                rtype = response.get("type")
                if rtype == "login_success":
                    self._on_login_success(response)
                    self._recv_loop(ws)
                elif rtype == "register_success":
                    self.recv_queue.put(("register_ok", response))
                    ws.close()
                else:
                    self.recv_queue.put(("auth_error", response.get("message", "Authentication failed")))
                    ws.close()
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    def _on_login_success(self, response):
        self._saved_username = response.get("username", self._saved_username)
        self._saved_password = self._saved_password
        self.username = response.get("username")
        self.is_admin = response.get("is_admin", False)
        self.is_super_admin = response.get("super_admin", False)

    def _recv_loop(self, ws):
        try:
            for raw in ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                self.recv_queue.put(("message", data))
        except Exception:
            if self.running:
                self.recv_queue.put(("disconnected", None))

    def _reconnect_thread(self):
        for attempt in range(45):
            if not self.running:
                return
            try:
                ws_mod = _get_websockets()
                with ws_mod.connect(self._saved_uri) as ws:
                    ws.send(json.dumps({
                        "type": "login", "username": self._saved_username,
                        "password": self._saved_password, "client_version": VERSION,
                    }))
                    response = json.loads(ws.recv())
                    if response.get("type") == "login_success":
                        self.ws = ws
                        self.connected = True
                        self._on_login_success(response)
                        self.recv_queue.put(("reconnected", response))
                        self._recv_loop(ws)
                        return
                    ws.close()
            except Exception:
                pass
            time.sleep(3)
        self.recv_queue.put(("reconnect_failed", None))

    # --- christmaschild OAuth login ------------------------------------- #
    def start_ccauth(self):
        threading.Thread(target=self._ccauth_flow, daemon=True).start()
        self._set_status("Opening browser for christmaschild authentication...")

    def _ccauth_flow(self):
        token_container = [None]
        state = secrets.token_urlsafe(32)
        port = random.randint(20000, 60000)

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                code = params.get("code", [""])[0]
                cb_state = params.get("state", [""])[0]
                if cb_state != state or not code:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch or missing code. Please try again.")
                    return
                try:
                    req = urllib.request.Request(
                        f"{AUTH_SERVER_URL}/api/auth/token",
                        data=json.dumps({"code": code}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                    token_container[0] = result.get("token")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<h2>Authentication successful!</h2>"
                                     b"<p>You may close this window and return to Chatwisp.</p>")
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(f"Authentication failed: {e}".encode("utf-8"))

            def do_OPTIONS(self):
                self.send_response(200)
                self.end_headers()

        try:
            server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
            server.timeout = 120
            redirect_uri = f"http://127.0.0.1:{port}/callback"
            auth_url = (
                f"{AUTH_SERVER_URL}/api/auth/authorize?service=chatwisp"
                f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
                f"&state={urllib.parse.quote(state, safe='')}"
            )
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass
            self._set_status("Waiting for authentication in browser...")
            while token_container[0] is None and self.running:
                server.handle_request()
            server.server_close()
            token = token_container[0]
            if token:
                self._ccauth_token = token
                self._set_status(f"Connecting to {self._server_uri}...")
                threading.Thread(
                    target=self._ccauth_ws_connect,
                    args=(self._server_uri or DEFAULT_URI, token),
                    daemon=True,
                ).start()
            else:
                self.recv_queue.put(("auth_error", "Authentication failed or was cancelled."))
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    def _ccauth_ws_connect(self, uri, token):
        try:
            ws_mod = _get_websockets()
            with ws_mod.connect(uri) as ws:
                self.ws = ws
                self.connected = True
                ws.send(json.dumps({"type": "login_ccauth", "token": token, "client_version": VERSION}))
                response = json.loads(ws.recv())
                rtype = response.get("type")
                if rtype == "login_success":
                    self._on_login_success(response)
                    self.recv_queue.put(("auth_success", response))
                    self._recv_loop(ws)
                elif rtype == "ccauth_new_user":
                    self.recv_queue.put(("ccauth_new_user", response))
                    ws.close()
                else:
                    self.recv_queue.put(("auth_error", response.get("message", "Authentication failed")))
                    ws.close()
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    def _ccauth_register(self, username):
        token = self._ccauth_token
        uri = self._server_uri or DEFAULT_URI
        self._set_status(f"Creating account {username}...")
        try:
            ws_mod = _get_websockets()
            with ws_mod.connect(uri) as ws:
                ws.send(json.dumps({
                    "type": "complete_ccauth_registration",
                    "token": token, "username": username, "client_version": VERSION,
                }))
                response = json.loads(ws.recv())
                if response.get("type") == "login_success":
                    self._on_login_success(response)
                    self.recv_queue.put(("auth_success", response))
                    self._recv_loop(ws)
                else:
                    self.recv_queue.put(("auth_error", response.get("message", "Registration failed")))
                    ws.close()
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    # ------------------------------------------------------------------ #
    #  Keepalive
    # ------------------------------------------------------------------ #
    def _maybe_keepalive(self):
        if self.connected and self.ws and time.time() - self._last_ping_time >= 30:
            self._last_ping_time = time.time()
            try:
                self.ws.send(json.dumps({"type": "ping", "client_time": time.time()}))
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Inbound message handling (runs in the main/UI thread while draining)
    # ------------------------------------------------------------------ #
    def _drain_recv(self):
        drained = False
        while True:
            try:
                msg = self.recv_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            try:
                self._handle_recv(msg)
            except Exception as e:
                self._set_status(f"Internal error: {e}")
        if drained:
            self.dirty = True

    def _handle_recv(self, msg):
        mtype, data = msg[0], msg[1]
        if mtype == "connection_error":
            self._set_status(f"Could not connect: {data}")
            self._show_alert(f"Could not connect: {data}")
        elif mtype == "auth_error":
            self._set_status(_safe_str(data) or "Authentication failed")
            self._show_alert(_safe_str(data) or "Authentication failed")
        elif mtype == "register_ok":
            self._set_status("Registration successful! You can now log in.")
            self._show_alert("Registration successful! You can now log in.")
        elif mtype == "auth_success":
            self._set_status(f"Welcome, {self.username}!")
            self.show_forums_view()
        elif mtype == "ccauth_new_user":
            self._handle_ccauth_new_user(data)
        elif mtype == "reconnected":
            self._reconnecting = False
            self._set_status("Reconnected!")
            self.show_forums_view()
        elif mtype == "reconnect_failed":
            self._reconnecting = False
            self._saved_username = None
            self._saved_password = None
            self._show_alert("Could not reconnect to server. Returning to login.")
            self.show_server_select()
        elif mtype == "disconnected":
            self.connected = False
            self.ws = None
            if (self._saved_username and self._saved_password and self._saved_uri
                    and not self._reconnecting):
                self._reconnecting = True
                self._set_status("Connection lost, reconnecting...")
                threading.Thread(target=self._reconnect_thread, daemon=True).start()
            else:
                self._show_alert("Lost connection to server")
                self.show_server_select()
        elif mtype == "message":
            self._handle_server_message(data)

    def _handle_server_message(self, data):
        dtype = data.get("type")
        if dtype == "welcome":
            self.motd = data.get("motd", "")
            self._set_status(data.get("message", "Welcome"))
            self._tts(data.get("message", "Welcome"))
        elif dtype == "forums_list":
            self.forums = data.get("forums", [])
            self.show_forums_view()
        elif dtype == "topics_list":
            self.topics = data.get("topics", [])
            if not self.forum_id_stack or self.forum_id_stack[-1] != data.get("forum_id"):
                self.forum_id_stack.append(data.get("forum_id"))
            self.show_topics_view()
        elif dtype == "posts_list":
            self.current_topic = data.get("topic", {})
            self.posts = data.get("posts", [])
            if not self.topic_id_stack or self.topic_id_stack[-1] != self.current_topic.get("id"):
                self.topic_id_stack.append(self.current_topic.get("id"))
            self.show_posts_view()
        elif dtype == "topic_created":
            self._set_status("Topic created")
            if self.forum_id_stack:
                self._send({"type": "get_topics", "forum_id": self.forum_id_stack[-1]})
        elif dtype == "post_created":
            self._set_status("Post created")
            if self.topic_id_stack:
                self._send({"type": "get_posts", "topic_id": self.topic_id_stack[-1]})
        elif dtype == "forum_created":
            self._set_status("Forum created")
            self._send({"type": "get_forums"})
        elif dtype in ("topic_closed", "topic_reopened"):
            self._set_status("Topic " + ("closed" if dtype == "topic_closed" else "reopened"))
            tid = data.get("topic_id")
            if self.topic_id_stack and self.topic_id_stack[-1] == tid:
                self._send({"type": "get_posts", "topic_id": tid})
            elif self.forum_id_stack:
                self._send({"type": "get_topics", "forum_id": self.forum_id_stack[-1]})
        elif dtype == "users_list":
            self.users = data.get("users", [])
            self.show_accounts_view()
        elif dtype == "banned":
            self._set_status(data.get("message", "User banned"))
        elif dtype == "unbanned":
            self._set_status(data.get("message", "User unbanned"))
        elif dtype == "user_deleted":
            self._set_status(data.get("message", "User deleted"))
            self._send({"type": "get_users"})
        elif dtype == "promoted":
            if data.get("username") == self.username:
                self.is_admin = True
            self._set_status(data.get("message", "Promoted"))
            self._show_alert(data.get("message", "Promoted"))
        elif dtype == "demoted":
            if data.get("username") == self.username:
                self.is_admin = False
            self._set_status(data.get("message", "Demoted"))
            self._show_alert(data.get("message", "Demoted"))
        elif dtype == "motd_set":
            self._set_status(data.get("message", "MOTD updated"))
            self._show_alert(data.get("message", "MOTD updated"))
        elif dtype == "unread_dms":
            self.unread_count = data.get("count", 0)
            if self.unread_count > 0:
                self._set_status(f"You have {self.unread_count} unread message(s)")
        elif dtype == "dm_contacts":
            self.dm_contacts = data.get("contacts", [])
            self.show_dm_list_view()
        elif dtype == "search_results":
            self._search_results = data.get("users", [])
            self.show_dm_search_view()
        elif dtype == "dm_conversation":
            self.dm_messages = data.get("messages", [])
            self.show_dm_chat_view()
        elif dtype == "dm_sent":
            self._set_status("Message sent")
            if self.dm_contact:
                self._send({"type": "get_dm_conversation", "username": self.dm_contact})
        elif dtype == "dm_received":
            dm = data.get("dm", {})
            other = dm.get("recipient") if dm.get("sender") == self.username else dm.get("sender")
            if self.view and self.view.get("name") == "dm_chat" and self.dm_contact == other:
                self._send({"type": "get_dm_conversation", "username": other})
                self._send({"type": "mark_dms_read", "username": other})
            else:
                self.unread_count += 1
                self._set_status(f"New message from {other}")
                self._tts(f"New message from {other}")
        elif dtype == "post_deleted":
            self._set_status("Post deleted")
            if self.topic_id_stack:
                self._send({"type": "get_posts", "topic_id": self.topic_id_stack[-1]})
        elif dtype == "topic_deleted":
            self._set_status("Topic deleted")
            if self.topic_id_stack:
                self.topic_id_stack.pop()
            if self.forum_id_stack:
                self._send({"type": "get_topics", "forum_id": self.forum_id_stack[-1]})
        elif dtype in ("topic_admin_only_set", "topic_admin_only_removed"):
            self._set_status("Admin-only " + ("set" if dtype == "topic_admin_only_set" else "removed"))
            if self.topic_id_stack:
                self._send({"type": "get_posts", "topic_id": self.topic_id_stack[-1]})
        elif dtype == "password_reset":
            self._set_status(data.get("message", "Password reset"))
            self._show_alert(data.get("message", "Password reset"))
        elif dtype == "pong":
            if self._pending_ping_time:
                rtt = int((time.time() - self._pending_ping_time) * 1000)
                self._pending_ping_time = 0
                m = f"Ping complete. The ping took {rtt} milliseconds."
                self._set_status(m)
                self._tts(m)
        elif dtype == "server_info":
            if self._pending_server_info:
                self._pending_server_info = False
                uptime = data.get("uptime", 0)
                days = uptime // 86400
                hours = (uptime % 86400) // 3600
                minutes = (uptime % 3600) // 60
                seconds = uptime % 60
                parts = []
                if days:
                    parts.append(f"{days} day(s)")
                if hours:
                    parts.append(f"{hours} hour(s)")
                if minutes:
                    parts.append(f"{minutes} minute(s)")
                parts.append(f"{seconds} second(s)")
                m = "Server has been up for " + ", ".join(parts)
                self._set_status(m)
                self._tts(m)
        elif dtype == "bot_dm_sent":
            self._set_status("Message sent as official account")
        elif dtype == "bot_broadcast_complete":
            self._set_status(data.get("message", "Broadcast complete"))
            self._show_alert(data.get("message", "Broadcast complete"))
        elif dtype == "bot_post_created":
            self._set_status("Post created as official account")
        elif dtype == "bot_topic_created":
            self._set_status("Topic created as official account")
            if self.forum_id_stack:
                self._send({"type": "get_topics", "forum_id": self.forum_id_stack[-1]})
        elif dtype == "signature_data":
            self.signature = data.get("signature", "")
            if self.view and self.view.get("name") == "settings":
                self.input_buf = self.signature
                self.dirty = True
        elif dtype == "signature_updated":
            self._set_status("Signature saved")
            self._show_alert("Signature updated")
        elif dtype == "error":
            self._show_alert(data.get("message", "Unknown error"))
            self._set_status(f"Error: {data.get('message', '')}")

    # ------------------------------------------------------------------ #
    #  Small UI helpers
    # ------------------------------------------------------------------ #
    def _set_status(self, text):
        self.status = _safe_str(text)
        self.dirty = True

    def _curs(self, on):
        try:
            curses.curs_set(1 if on else 0)
        except Exception:
            pass

    def _tts(self, text):
        _tts_speak(text)

    def _show_alert(self, text):
        """Show a blocking alert; the main loop pops it after a key."""
        self._alert_text = _safe_str(text)
        self.dirty = True

    def _alert_text(self):
        return getattr(self, "_alert_msg", None)

    _alert_msg = None

    def _push_view(self, view):
        self.view_stack.append(view)
        self.view = view
        self.scroll = 0
        self.cursor = 0
        self.input_buf = ""
        self.input_active = False
        self.input_label = ""
        self._lines = []
        self._line_meta = []
        self.dirty = True

    def _pop_view(self):
        if len(self.view_stack) > 1:
            self.view_stack.pop()
            self.view = self.view_stack[-1]
            self.scroll = 0
            self.cursor = 0
            self.input_active = False
            self.dirty = True
        else:
            self.running = False

    def _home(self):
        if self.connected:
            self._send({"type": "get_forums"})
        else:
            self.show_server_select()

    # ------------------------------------------------------------------ #
    #  Views
    # ------------------------------------------------------------------ #
    def show_server_select(self):
        self._push_view({"name": "server_select", "items": [
            ("Connect to Central Server", "central"),
            ("Connect to External Server", "external"),
            ("Sign in with christmaschild Account", "ccauth"),
            ("Quit", "quit"),
        ]})

    def show_external_server_view(self):
        self._push_view({"name": "external_server",
                         "fields": [("Server address", "127.0.0.1", False),
                                    ("Port", "8765", False)],
                         "field": 0})

    def show_login_view(self):
        self._push_view({"name": "login",
                         "fields": [("Username", "", False),
                                    ("Password", "", True)],
                         "field": 0})

    def show_forums_view(self):
        self._push_view({"name": "forums"})

    def show_topics_view(self):
        self._push_view({"name": "topics"})

    def show_posts_view(self):
        self._push_view({"name": "posts"})

    def show_accounts_view(self):
        self._push_view({"name": "accounts"})

    def show_user_detail_view(self, user):
        self._push_view({"name": "user_detail", "user": user})

    def show_dm_list_view(self):
        self._push_view({"name": "dm_list"})

    def show_dm_search_view(self):
        self._push_view({"name": "dm_search", "results": getattr(self, "_search_results", [])})

    def show_dm_chat_view(self):
        self._push_view({"name": "dm_chat"})

    def show_settings_view(self):
        self._push_view({"name": "settings"})
        self.input_label = "Signature"
        self.input_buf = self.signature
        self._send({"type": "get_signature"})

    def show_bot_view(self):
        self._push_view({"name": "bot", "items": [
            ("Send DM as Official Account", "bot_dm"),
            ("Broadcast to All Users", "bot_broadcast"),
            ("Create Post as Official Account", "bot_post"),
            ("Create Topic as Official Account", "bot_topic"),
        ]})

    def show_form_view(self, title, fields, submit_type):
        """fields: list of (label, default, secret); submit_type identifies the action."""
        self._push_view({"name": "form", "title": title, "fields": fields,
                         "field": 0, "submit": submit_type})

    # ------------------------------------------------------------------ #
    #  Rendering
    # ------------------------------------------------------------------ #
    def _build_lines(self):
        """Populate self._lines / self._line_meta for the current view."""
        self._lines = []
        self._line_meta = []
        name = self.view["name"]
        width = max(10, getattr(curses, "COLS", 80) - 2)

        if name == "server_select":
            for i, (label, _) in enumerate(self.view["items"]):
                self._lines.append(f"  {i + 1}. {label}")
                self._line_meta.append(("item", i))
        elif name == "external_server" or name == "login" or name == "form":
            pass  # forms render fields directly, not as _lines
        elif name == "forums":
            if not self.forums:
                self._lines.append("  (no forums)")
            for i, f in enumerate(self.forums):
                self._lines.append(f"  {i + 1}. {f.get('name', '')}")
                self._line_meta.append(("forum", i))
                desc = f.get("description", "")
                if desc:
                    for ln in textwrap.wrap(desc, width - 4):
                        self._lines.append("      " + ln)
                        self._line_meta.append(("forum", i))
        elif name == "topics":
            if not self.topics:
                self._lines.append("  (no topics)")
            for i, t in enumerate(self.topics):
                tag = " [CLOSED]" if t.get("closed") else ""
                tag += " [ADMIN ONLY]" if t.get("admin_only") else ""
                self._lines.append(f"  {i + 1}. {t.get('title', '')} by {t.get('author', '')}{tag} ({t.get('post_count', 0)} posts)")
                self._line_meta.append(("topic", i))
        elif name == "posts":
            t = self.current_topic or {}
            tag = " [CLOSED]" if t.get("closed") else ""
            tag += " [ADMIN ONLY]" if t.get("admin_only") else ""
            self._lines.append(f"Topic: {t.get('title', '')}{tag}")
            self._line_meta.append(("header", None))
            self._lines.append("")
            self._line_meta.append(("header", None))
            if not self.posts:
                self._lines.append("  (no posts yet)")
                self._line_meta.append(("post", None))
            for i, p in enumerate(self.posts):
                sig = p.get("signature")
                body = p.get("content", "")
                if sig:
                    body = body + f"\n— {sig}"
                self._lines.append(f"[{i + 1}] {p.get('author', '')} said:")
                self._line_meta.append(("post", i))
                for ln in textwrap.wrap(body, width - 2) or [""]:
                    self._lines.append("  " + ln)
                    self._line_meta.append(("post", i))
                self._lines.append("")
                self._line_meta.append(("post", i))
        elif name == "accounts":
            if not self.users:
                self._lines.append("  (no users)")
            for i, u in enumerate(self.users):
                parts = [u.get("username", "")]
                if u.get("is_admin"):
                    parts.append("[Admin]")
                if u.get("super_admin"):
                    parts.append("[Super]")
                if u.get("banned"):
                    parts.append(f"[Banned: {u.get('ban_reason') or 'no reason'}]")
                self._lines.append(f"  {i + 1}. " + " ".join(parts))
                self._line_meta.append(("user", i))
        elif name == "user_detail":
            u = self.view["user"]
            self._lines.append(f"Username: {u.get('username', '')}")
            self._line_meta.append(("info", None))
            self._lines.append(f"Admin: {'Yes' if u.get('is_admin') else 'No'}"
                               f"  Super: {'Yes' if u.get('super_admin') else 'No'}"
                               f"  Banned: {'Yes' if u.get('banned') else 'No'}")
            self._line_meta.append(("info", None))
            self._lines.append("")
            self._line_meta.append(("info", None))
            acts = self._user_actions(u)
            for i, (label, _) in enumerate(acts):
                self._lines.append(f"  {i + 1}. {label}")
                self._line_meta.append(("action", i))
        elif name == "dm_list":
            if not self.dm_contacts:
                self._lines.append("  (no conversations)")
            for i, c in enumerate(self.dm_contacts):
                self._lines.append(f"  {i + 1}. {c.get('username', '')}: {c.get('last_message', '')}")
                self._line_meta.append(("dm", i))
        elif name == "dm_search":
            res = self.view.get("results", [])
            if not res:
                self._lines.append("  (type a name to search, Enter to open)")
            for i, uname in enumerate(res):
                self._lines.append(f"  {i + 1}. {uname}")
                self._line_meta.append(("dmuser", i))
        elif name == "dm_chat":
            if not self.dm_messages:
                self._lines.append("  (no messages yet)")
                self._line_meta.append(("msg", None))
            for m in self.dm_messages:
                who = "You" if m.get("sender") == self.username else m.get("sender", "")
                for ln in textwrap.wrap(f"{who}: {m.get('content', '')}", width - 2) or [""]:
                    self._lines.append("  " + ln)
                    self._line_meta.append(("msg", None))
        elif name == "settings":
            self._lines.append("Forum signature (appended to your posts, max 50 chars):")
            self._line_meta.append(("info", None))
            self._lines.append("")
            self._line_meta.append(("info", None))
        elif name == "bot":
            for i, (label, _) in enumerate(self.view["items"]):
                self._lines.append(f"  {i + 1}. {label}")
                self._line_meta.append(("bot", i))

    def _user_actions(self, u):
        acts = []
        if not u.get("banned"):
            acts.append(("Ban user", "ban"))
        else:
            acts.append(("Unban user", "unban"))
        if u.get("username") != self.username:
            acts.append(("Delete user", "delete"))
        if self.is_super_admin and not u.get("super_admin") and u.get("username") != self.username:
            if not u.get("is_admin"):
                acts.append(("Promote to admin", "promote"))
            else:
                acts.append(("Demote from admin", "demote"))
        if self.is_admin and u.get("username") != self.username:
            acts.append(("Reset password", "resetpw"))
        acts.append(("Back", "back"))
        return acts

    def render(self):
        if self.stdscr is None:
            return
        stdscr = self.stdscr
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        name = self.view["name"] if self.view else ""

        # Title
        title = self._view_title()
        stdscr.addstr(0, 0, title[: w - 1], curses.A_REVERSE)
        stdscr.clrtoeol()

        body_top = 1
        body_bottom = h - 3  # leave room for input + status + help
        body_h = body_bottom - body_top
        if body_h < 1:
            body_h = 1

        # Forms render fields directly
        if name in ("login", "external_server", "form"):
            self._render_form(stdscr, body_top, w)
        else:
            self._build_lines()
            n = len(self._lines)
            if self.cursor >= n:
                self.cursor = max(0, n - 1)
            if self.cursor < self.scroll:
                self.scroll = self.cursor
            elif self.cursor >= self.scroll + body_h:
                self.scroll = self.cursor - body_h + 1
            for i in range(body_h):
                idx = self.scroll + i
                if idx >= n:
                    break
                attr = curses.A_REVERSE if idx == self.cursor else curses.A_NORMAL
                line = self._lines[idx]
                stdscr.addstr(body_top + i, 0, line[: w - 1], attr)
                stdscr.clrtoeol()
            # input line for list views that accept inline input
            if self.input_active and name in ("posts", "dm_chat", "settings"):
                self._render_input(stdscr, h - 3, w)

        # Status bar
        status = self.status
        if self._reconnecting:
            status = "Reconnecting..."
        stdscr.addstr(h - 2, 0, " " * (w - 1))
        stdscr.addstr(h - 2, 0, status[: w - 1], curses.A_BOLD)
        stdscr.clrtoeol()

        # Help line
        stdscr.addstr(h - 1, 0, self._help_text()[: w - 1], curses.A_DIM)
        stdscr.clrtoeol()

        # Alert overlay
        if getattr(self, "_alert_msg", None):
            msg = self._alert_msg
            box_w = min(w - 4, max(20, len(msg) + 4))
            box_h = 3
            by = (h - box_h) // 2
            bx = (w - box_w) // 2
            try:
                win = curses.newwin(box_h, box_w, by, bx)
                win.border()
                win.addstr(1, 2, msg[: box_w - 4], curses.A_BOLD)
                win.refresh()
            except curses.error:
                stdscr.addstr(h - 3, 0, msg[: w - 1], curses.A_BOLD)

        stdscr.refresh()
        self.dirty = False

    def _view_title(self):
        name = self.view["name"] if self.view else ""
        if name == "server_select":
            return " Chatwisp — Select Server "
        if name == "external_server":
            return " External Server "
        if name == "login":
            return f" Chatwisp — Login / Register   (Server: {self._server_uri}) "
        if name == "forums":
            who = f"logged in as {self.username}" if self.username else ""
            adm = " [ADMIN]" if self.is_admin else ""
            return f" Select Forum   ({who}){adm} "
        if name == "topics":
            return " Topics "
        if name == "posts":
            t = self.current_topic or {}
            return f" Posts — {t.get('title', '')} "
        if name == "accounts":
            return " User Accounts (Admin) "
        if name == "user_detail":
            return f" User: {self.view['user'].get('username', '')} "
        if name == "dm_list":
            return " Messages "
        if name == "dm_search":
            return " Search Users "
        if name == "dm_chat":
            return f" Chat with {self.dm_contact} "
        if name == "settings":
            return " Settings "
        if name == "bot":
            return " Official Account Controls "
        if name == "form":
            return f" {self.view.get('title', 'Form')} "
        return " Chatwisp "

    def _render_form(self, stdscr, top, w):
        fields = self.view["fields"]
        cur = self.view["field"]
        for i, (label, val, secret) in enumerate(fields):
            row = top + i * 2
            stdscr.addstr(row, 0, label[: w - 1])
            disp = val
            if secret:
                disp = "*" * len(val)
            attr = curses.A_REVERSE if i == cur else curses.A_NORMAL
            stdscr.addstr(row + 1, 0, "  " + disp[: w - 3], attr)
            stdscr.clrtoeol()
        # hint
        stdscr.addstr(top + len(fields) * 2, 0,
                      "Tab/Up/Down: switch field · Enter: submit · Esc: cancel"[: w - 1],
                      curses.A_DIM)

    def _render_input(self, stdscr, row, w):
        label = self.input_label
        stdscr.addstr(row, 0, " " * (w - 1))
        stdscr.addstr(row, 0, f"{label}: {self.input_buf}", curses.A_NORMAL)
        stdscr.clrtoeol()
        self._curs(1)
        try:
            stdscr.move(row, min(len(label) + 2 + len(self.input_buf), w - 2))
        except curses.error:
            pass

    def _help_text(self):
        name = self.view["name"] if self.view else ""
        if name == "server_select":
            return "Up/Down: choose · Enter: select"
        if name in ("login", "external_server", "form"):
            return "Tab/↑↓: field · Enter: submit · Esc: back"
        if name == "forums":
            extra = " · f: new forum · m: messages · s: settings" if self.is_admin else " · m: messages · s: settings"
            return "↑↓: choose · Enter: open · " + ("a: accounts · " if self.is_admin else "") + "o: official" + extra + " · F1: info · F2: ping · q: quit"
        if name == "topics":
            adm = " · c/o: close/open · D: delete · C-n: new topic" if self.is_admin else " · C-n: new topic"
            return "↑↓: choose · Enter: open · Esc: home" + adm
        if name == "posts":
            adm = " · c/o: close/open · a: admin-only · D: del topic · x: del post" if self.is_admin else ""
            return "↑↓: scroll · r: reply · l: copy link · Esc: back" + adm
        if name == "accounts":
            return "↑↓: choose · Enter: actions · Esc: home"
        if name == "user_detail":
            return "↑↓: choose · Enter: do action · Esc: back"
        if name == "dm_list":
            return "↑↓: choose · Enter: open · n: new message · Esc: home"
        if name == "dm_search":
            return "type to search · ↑↓: choose · Enter: open chat · Esc: back"
        if name == "dm_chat":
            return "i: type message · Enter: send · Esc: back"
        if name == "settings":
            return "i: edit signature · Enter: save · Esc: back"
        if name == "bot":
            return "↑↓: choose · Enter: select · Esc: home"
        return "Esc: back · q: quit"

    # ------------------------------------------------------------------ #
    #  Key handling
    # ------------------------------------------------------------------ #
    def on_key(self, ch):
        # Dismiss alert first
        if getattr(self, "_alert_msg", None):
            self._alert_msg = None
            self.dirty = True
            return

        name = self.view["name"] if self.view else ""

        # Global keys
        if ch == curses.KEY_RESIZE:
            self.dirty = True
            return
        if ch in (ord("q"),) and name in ("server_select", "forums"):
            self.running = False
            return

        # Inline input mode (for posts reply, dm chat, settings)
        if self.input_active and name in ("posts", "dm_chat", "settings"):
            self._handle_input_key(ch, name)
            return

        # Form views
        if name in ("login", "external_server", "form"):
            self._handle_form_key(ch)
            return

        # List views
        if ch in (curses.KEY_UP, ord("k")):
            if self.cursor > 0:
                self.cursor -= 1
            self.dirty = True
            return
        if ch in (curses.KEY_DOWN, ord("j")):
            if self.cursor < max(0, len(self._lines) - 1):
                self.cursor += 1
            self.dirty = True
            return
        if ch in (27, curses.KEY_BACKSPACE):
            self._pop_view()
            return

        # Per-view activation
        self._handle_view_key(name, ch)

    def _handle_input_key(self, ch, name):
        if ch in (27,):
            self.input_active = False
            self.input_buf = ""
            self._curs(0)
            self.dirty = True
            return
        if ch in (10, 13):
            self._submit_input(name)
            return
        if ch in (curses.KEY_BACKSPACE, 8, 127):
            if self.input_buf:
                self.input_buf = self.input_buf[:-1]
            self.dirty = True
            return
        if 32 <= ch <= 126:
            limit = 50 if name == "settings" else 2000
            if len(self.input_buf) < limit:
                self.input_buf += chr(ch)
            self.dirty = True
            return

    def _submit_input(self, name):
        text = self.input_buf.strip()
        if name == "posts":
            if not text:
                return
            tid = self.topic_id_stack[-1] if self.topic_id_stack else None
            if tid:
                self._send({"type": "create_post", "topic_id": tid, "content": text})
                self.input_buf = ""
                self.input_active = False
                self._curs(0)
                self._set_status("Sending reply...")
        elif name == "dm_chat":
            if not text or not self.dm_contact:
                return
            if self.dm_contact == "Chatwisp Official Account":
                self._set_status("You cannot reply to the official account")
                self.input_buf = ""
                return
            self._send({"type": "send_dm", "recipient": self.dm_contact, "content": text})
            self.input_buf = ""
            self.input_active = False
            self._curs(0)
            self._set_status("Sending message...")
        elif name == "settings":
            if len(self.input_buf) > 50:
                self._set_status("Signature must be 50 characters or less")
                return
            self._send({"type": "set_signature", "signature": self.input_buf.strip()})
            self._set_status("Saving signature...")
    def _handle_form_key(self, ch):
        fields = self.view["fields"]
        cur = self.view["field"]
        if ch in (curses.KEY_UP,):
            self.view["field"] = (cur - 1) % len(fields)
            self.dirty = True
            return
        if ch in (curses.KEY_DOWN, ord("\t"), 9, curses.KEY_TAB):
            self.view["field"] = (cur + 1) % len(fields)
            self.dirty = True
            return
        if ch in (27,):
            self._pop_view()
            return
        if ch in (10, 13):
            self._submit_form()
            return
        if ch in (curses.KEY_BACKSPACE, 8, 127):
            label, val, secret = fields[cur]
            if val:
                fields[cur] = (label, val[:-1], secret)
            self.dirty = True
            return
        if 32 <= ch <= 126:
            label, val, secret = fields[cur]
            if len(val) < 200:
                fields[cur] = (label, val + chr(ch), secret)
            self.dirty = True
            return

    def _submit_form(self):
        name = self.view["name"]
        fields = self.view["fields"]
        vals = [f[1].strip() for f in fields]
        if name == "login":
            username, password = vals[0], fields[1][1]
            if not username or not password:
                self._set_status("Username and password required")
                return
            self._saved_uri = self._server_uri
            self._set_status(f"Connecting to {self._server_uri}...")
            threading.Thread(target=self._connect,
                             args=(self._server_uri, username, password, "login"),
                             daemon=True).start()
        elif name == "external_server":
            host, port = vals[0], vals[1]
            if not host or not port:
                self._set_status("Server address and port required")
                return
            self._server_uri = f"ws://{host}:{port}"
            self.show_login_view()
        elif name == "form":
            self._submit_generic_form(self.view.get("submit", ""), vals, fields)

    def _submit_generic_form(self, submit, vals, fields):
        if submit == "register":
            username, password = vals[0], fields[1][1]
            if len(username) < 3:
                self._set_status("Username must be at least 3 characters")
                return
            if len(password) < 8:
                self._set_status("Password must be at least 8 characters")
                return
            self._saved_uri = self._server_uri
            self._set_status(f"Connecting to {self._server_uri}...")
            threading.Thread(target=self._connect,
                             args=(self._server_uri, username, password, "register"),
                             daemon=True).start()
        elif submit == "create_topic":
            title, content = vals[0], vals[1]
            if not title:
                self._set_status("Title required")
                return
            fid = self.forum_id_stack[-1] if self.forum_id_stack else None
            self._send({"type": "create_topic", "forum_id": fid, "title": title, "content": content})
            self._pop_view()
            self._set_status("Creating topic...")
        elif submit == "create_forum":
            name_, desc = vals[0], vals[1]
            if not name_:
                self._set_status("Forum name required")
                return
            self._send({"type": "create_forum", "name": name_, "description": desc})
            self._pop_view()
            self._set_status("Creating forum...")
        elif submit == "set_motd":
            motd = vals[0]
            if not motd:
                self._set_status("MOTD required")
                return
            self._send({"type": "set_motd", "motd": motd})
            self._pop_view()
            self._set_status("Setting MOTD...")
        elif submit == "ban":
            reason = vals[0] or None
            duration = vals[1] or None
            user = self.view.get("user")
            self._send({"type": "ban_user", "username": user.get("username"),
                        "reason": reason, "duration": duration})
            self._pop_view()
            self._set_status("Banning user...")
        elif submit == "resetpw":
            p1, p2 = fields[0][1], fields[1][1]
            if len(p1) < 8:
                self._set_status("Password must be at least 8 characters")
                return
            if p1 != p2:
                self._set_status("Passwords do not match")
                return
            user = self.view.get("user")
            self._send({"type": "reset_password", "username": user.get("username"), "new_password": p1})
            self._pop_view()
            self._set_status("Resetting password...")
        elif submit == "bot_dm":
            recipient, content = vals[0], vals[1]
            if not recipient or not content:
                self._set_status("Recipient and content required")
                return
            self._send({"type": "bot_send_dm", "recipient": recipient, "content": content})
            self._pop_view()
            self._set_status("Sending DM as official account...")
        elif submit == "bot_broadcast":
            content = vals[0]
            if not content:
                self._set_status("Content required")
                return
            self._send({"type": "bot_broadcast", "content": content})
            self._pop_view()
            self._set_status("Broadcasting...")
        elif submit == "bot_post":
            topic_id, content = vals[0], vals[1]
            if not topic_id or not content:
                self._set_status("Topic ID and content required")
                return
            self._send({"type": "bot_create_post", "topic_id": topic_id, "content": content})
            self._pop_view()
            self._set_status("Creating post as official account...")
        elif submit == "bot_topic":
            forum_id, title, content = vals[0], vals[1], vals[2]
            if not forum_id or not title:
                self._set_status("Forum ID and title required")
                return
            self._send({"type": "bot_create_topic", "forum_id": forum_id, "title": title, "content": content})
            self._pop_view()
            self._set_status("Creating topic as official account...")

    def _handle_view_key(self, name, ch):
        if ch in (10, 13, curses.KEY_RIGHT):
            self._activate_current(name)
            return
        if ch in (ord("f"), ord("F")):
            if name == "forums" and self.is_admin:
                self.show_form_view("Create New Forum",
                                    [("Forum name", "", False), ("Description", "", False)], "create_forum")
            return
        if ch == ord("m") and name == "forums":
            self._send({"type": "get_dm_contacts"})
            self._set_status("Loading messages...")
            return
        if ch == ord("s") and name == "forums":
            self.show_settings_view()
            return
        if ch == ord("a") and name == "forums" and self.is_admin:
            self._send({"type": "get_users"})
            self._set_status("Loading accounts...")
            return
        if ch == ord("o") and name == "forums" and self.is_admin:
            self.show_bot_view()
            return
        if ch == ord("n") and name == "topics":
            self.show_form_view("Create New Topic",
                                [("Topic title", "", False), ("First post (optional)", "", False)], "create_topic")
            return
        if ch == ord("n") and name == "dm_list":
            self._new_message_search()
            return
        if ch in (ord("i"), ord("n")) and name == "dm_search":
            self._new_message_search()
            return
        if ch == ord("r") and name == "posts":
            t = self.current_topic or {}
            if t.get("closed"):
                self._set_status("Cannot reply in a closed topic")
                return
            if t.get("admin_only") and not self.is_admin:
                self._set_status("Only admins can post in this topic")
                return
            self.input_active = True
            self.input_label = "Reply"
            self.input_buf = ""
            return
        if ch == ord("i") and name in ("dm_chat", "settings"):
            if name == "dm_chat" and self.dm_contact == "Chatwisp Official Account":
                self._set_status("You cannot reply to the official account")
                return
            self.input_active = True
            self.input_label = "Message" if name == "dm_chat" else "Signature"
            self.input_buf = self.input_buf if self.input_buf else (self.signature if name == "settings" else "")
            return
        if ch == ord("l") and name == "posts":
            self._copy_topic_link()
            return
        # Admin topic actions
        if self.is_admin and name in ("topics", "posts"):
            if ch == ord("c"):
                tid = self.topic_id_stack[-1] if self.topic_id_stack else None
                if tid:
                    self._send({"type": "close_topic", "topic_id": tid})
                    self._set_status("Closing topic...")
            elif ch == ord("o"):
                tid = self.topic_id_stack[-1] if self.topic_id_stack else None
                if tid:
                    self._send({"type": "reopen_topic", "topic_id": tid})
                    self._set_status("Reopening topic...")
            elif ch == ord("D"):
                tid = self.topic_id_stack[-1] if self.topic_id_stack else None
                if tid and self.stdscr and _confirm(self.stdscr, "Delete this entire topic and all posts? (y/N)"):
                    self._send({"type": "delete_topic", "topic_id": tid})
                    self._set_status("Deleting topic...")
            elif ch == ord("a") and name == "posts":
                tid = self.topic_id_stack[-1] if self.topic_id_stack else None
                t = self.current_topic or {}
                if tid:
                    if t.get("admin_only"):
                        self._send({"type": "remove_topic_admin_only", "topic_id": tid})
                    else:
                        self._send({"type": "set_topic_admin_only", "topic_id": tid})
                    self._set_status("Toggling admin only...")
            elif ch == ord("x") and name == "posts":
                self._admin_delete_post()
        if ch == curses.KEY_F1:
            self._pending_server_info = True
            self._tts("Retrieving server info")
            self._send({"type": "server_info"})
        if ch == curses.KEY_F2:
            self._pending_ping_time = time.time()
            self._tts("Pinging...")
            self._send({"type": "ping", "client_time": self._pending_ping_time})

    def _activate_current(self, name):
        if not self._line_meta:
            return
        idx = self.cursor if 0 <= self.cursor < len(self._line_meta) else 0
        kind, i = self._line_meta[idx]
        if name == "server_select":
            choice = self.view["items"][i][1]
            if choice == "central":
                self._server_uri = DEFAULT_URI
                self.show_login_view()
            elif choice == "external":
                self.show_external_server_view()
            elif choice == "ccauth":
                self._server_uri = DEFAULT_URI
                if not websockets_available():
                    self._show_alert("websockets library not found. Run: pip install websockets")
                    return
                self.start_ccauth()
            elif choice == "quit":
                self.running = False
        elif name == "forums":
            if kind == "forum" and i < len(self.forums):
                fid = self.forums[i].get("id")
                self.forum_id_stack.append(fid)
                self._set_status("Loading topics...")
                self._send({"type": "get_topics", "forum_id": fid})
        elif name == "topics":
            if kind == "topic" and i < len(self.topics):
                tid = self.topics[i].get("id")
                self._set_status("Loading posts...")
                self._send({"type": "get_posts", "topic_id": tid})
        elif name == "accounts":
            if kind == "user" and i < len(self.users):
                self.show_user_detail_view(self.users[i])
        elif name == "user_detail":
            if kind == "action":
                acts = self._user_actions(self.view["user"])
                action = acts[i][1]
                self._do_user_action(action, self.view["user"])
        elif name == "dm_list":
            if kind == "dm" and i < len(self.dm_contacts):
                self.dm_contact = self.dm_contacts[i].get("username")
                self._open_dm_chat()
        elif name == "dm_search":
            res = self.view.get("results", [])
            if kind == "dmuser" and i < len(res):
                self.dm_contact = res[i]
                self._open_dm_chat()
        elif name == "bot":
            if kind == "bot":
                choice = self.view["items"][i][1]
                if choice == "bot_dm":
                    self.show_form_view("Send DM as Official Account",
                                        [("Recipient", "", False), ("Message", "", False)], "bot_dm")
                elif choice == "bot_broadcast":
                    self.show_form_view("Broadcast to All Users",
                                        [("Message", "", False)], "bot_broadcast")
                elif choice == "bot_post":
                    self.show_form_view("Create Post as Official Account",
                                        [("Topic ID", "", False), ("Content", "", False)], "bot_post")
                elif choice == "bot_topic":
                    self.show_form_view("Create Topic as Official Account",
                                        [("Forum ID", "", False), ("Title", "", False), ("Content", "", False)],
                                        "bot_topic")

    def _new_message_search(self):
        if self.stdscr is None:
            return
        q = _prompt(self.stdscr, "Search users:")
        if q and q.strip():
            self._send({"type": "search_users", "query": q.strip()})
            self._set_status("Searching...")

    def _open_dm_chat(self):
        self.show_dm_chat_view()
        self._send({"type": "get_dm_conversation", "username": self.dm_contact})
        self._send({"type": "mark_dms_read", "username": self.dm_contact})

    def _do_dm_search(self):
        q = self.input_buf.strip()
        if q:
            self._send({"type": "search_users", "query": q})
        else:
            self._search_results = []
            self.view["results"] = []
            self.dirty = True

    def _do_user_action(self, action, user):
        uname = user.get("username")
        if action == "back":
            self._pop_view()
            return
        if action == "unban":
            self._send({"type": "unban_user", "username": uname})
            self._pop_view()
            self._set_status("Unbanning user...")
        elif action == "delete":
            if self.stdscr and _confirm(self.stdscr, f"Delete user '{uname}'? This cannot be undone. (y/N)"):
                self._send({"type": "delete_user", "username": uname})
                self._pop_view()
                self._set_status("Deleting user...")
        elif action == "promote":
            self._send({"type": "promote_admin", "username": uname})
            self._pop_view()
            self._set_status("Promoting user...")
        elif action == "demote":
            self._send({"type": "demote_admin", "username": uname})
            self._pop_view()
            self._set_status("Demoting user...")
        elif action == "ban":
            self.show_form_view(f"Ban {uname}",
                                [("Reason (optional)", "", False), ("Duration (optional, blank=infinite)", "", False)],
                                "ban")
        elif action == "resetpw":
            self.show_form_view(f"Reset password for {uname}",
                                [("New password", "", True), ("Confirm password", "", True)], "resetpw")

    def _admin_delete_post(self):
        # find the post the cursor currently sits on
        if not self._line_meta:
            return
        idx = self.cursor if 0 <= self.cursor < len(self._line_meta) else 0
        kind, pidx = self._line_meta[idx]
        if kind != "post" or pidx is None or pidx >= len(self.posts):
            self._set_status("Select a post first")
            return
        post = self.posts[pidx]
        if self.stdscr and _confirm(self.stdscr, f"Delete post by {post.get('author')}? (y/N)"):
            self._send({"type": "delete_post", "post_id": post.get("id")})
            self._set_status("Deleting post...")

    def _copy_topic_link(self):
        t = self.current_topic or {}
        forum_id = t.get("forum_id", "")
        slug = t.get("slug", "")
        if not forum_id or not slug:
            self._set_status("Topic link not available")
            return
        link = f"https://chatwisp.onrender.com/forums/{forum_id}/{slug}"
        try:
            # xclip / xsel if available
            for tool in ("xclip", "xsel"):
                if shutil.which(tool):
                    subprocess.Popen([tool], stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).stdin.write(
                        link.encode())
                    self._set_status("Topic link copied to clipboard")
                    return
            self._set_status(f"Link: {link}")
        except Exception:
            self._set_status(f"Link: {link}")

    def _handle_ccauth_new_user(self, data):
        email = data.get("email", "")
        if self.stdscr is None:
            return
        uname = _prompt(self.stdscr,
                        f"New account — choose a username (email: {email[:24]}):")
        if uname and len(uname) >= 3:
            threading.Thread(target=self._ccauth_register, args=(uname,), daemon=True).start()
        else:
            self._set_status("Username must be at least 3 characters")

    # ------------------------------------------------------------------ #
    #  Main loop
    # ------------------------------------------------------------------ #
    def run(self, stdscr):
        self.stdscr = stdscr
        stdscr.keypad(True)
        curses.noecho()
        self._curs(0)
        stdscr.timeout(100)
        self.show_server_select()
        while self.running:
            self._drain_recv()
            self._maybe_keepalive()
            if self.dirty:
                self.render()
            ch = stdscr.getch()
            if ch != -1:
                self.on_key(ch)
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass


def main(stdscr):
    if not websockets_available():
        stdscr.addstr(0, 0, "websockets library not found. Run: pip install websockets")
        stdscr.addstr(1, 0, "Press any key to exit.")
        stdscr.getch()
        return
    app = ChatwispApp()
    try:
        app.run(stdscr)
    finally:
        curses.endwin()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        sys.stderr.write(
            "Chatwisp could not initialize the terminal interface.\n"
            "Please run this program inside a real terminal (not piped or redirected).\n"
            f"curses error: {exc}\n"
        )
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write(f"Chatwisp encountered an error: {exc}\n")
        sys.exit(1)
