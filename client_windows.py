#!/usr/bin/env python3
import wx

VERSION = "4.0.0"
import json
import threading
import queue
import sys
import os
import time
import ctypes

try:
    import websockets.sync.client
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

MUSIC_AVAILABLE = False
try:
    import pygame
    pygame.mixer.init()
    MUSIC_AVAILABLE = True
except ImportError:
    pass

if getattr(sys, 'frozen', False):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MUSIC_DIR = os.path.join(_BASE_DIR, "music")
AVAILABLE_SONGS = ["ByTheFire", "Frozen-in-Time", "Noisescape", "TranquilReflections", "Wonder"]


class ChatwispFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=f"Chatwisp version {VERSION}", size=(800, 600))
        self.SetMinSize((600, 400))

        self.statusbar = self.CreateStatusBar()
        self.statusbar.SetStatusText("Welcome to Chatwisp")

        self.main_panel = wx.Panel(self)
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.main_panel.SetSizer(self.main_sizer)

        self.ws = None
        self.connected = False
        self.username = None
        self.is_admin = False
        self.is_super_admin = False
        self.unread_count = 0
        self.dm_contact = None
        self.current_topic_data = None
        self.forum_id_stack = []
        self.topic_id_stack = []
        self.current_view = None
        self.music_prefs = {}
        self.music_category = None

        self.recv_queue = queue.Queue()
        self.send_queue = queue.Queue()
        self.running = True

        self.view_panel = None
        self._tts_sapi = None
        self._pending_ping_time = 0
        self._pending_server_info = False
        self.recv_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_poll_recv, self.recv_timer)

        self.ID_NEW = wx.NewIdRef()
        self.ID_CLOSE = wx.NewIdRef()
        self.ID_REOPEN = wx.NewIdRef()
        accel = wx.AcceleratorTable([
            (wx.ACCEL_CTRL, ord('N'), self.ID_NEW),
            (wx.ACCEL_CTRL, ord('K'), self.ID_CLOSE),
            (wx.ACCEL_CTRL, ord('O'), self.ID_REOPEN),
        ])
        self.SetAcceleratorTable(accel)
        self.Bind(wx.EVT_MENU, self.on_ctrl_n, id=self.ID_NEW)
        self.Bind(wx.EVT_MENU, self.on_ctrl_k, id=self.ID_CLOSE)
        self.Bind(wx.EVT_MENU, self.on_ctrl_o, id=self.ID_REOPEN)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        self.show_login()
        self.recv_timer.Start(100)

    def announce(self, message):
        self.statusbar.SetStatusText(message)

    def _tts_speak(self, text):
        for dll_name in ("nvdaControllerClient64", "nvdaControllerClient"):
            try:
                nvda = getattr(ctypes.windll, dll_name)
                nvda.nvdaController_speakText(text)
                return
            except:
                continue
        if self._tts_sapi is None:
            try:
                import win32com.client
                self._tts_sapi = win32com.client.Dispatch("SAPI.SpVoice")
            except:
                self._tts_sapi = False
        if self._tts_sapi:
            self._tts_sapi.Speak(text)

    def _play_music(self, category):
        song = self.music_prefs.get(category)
        if song is None:
            song = "ByTheFire"
        if not song or not MUSIC_AVAILABLE:
            self._stop_music()
            return
        if self.music_category == category:
            return
        path = os.path.join(MUSIC_DIR, song + ".mp3")
        if not os.path.exists(path):
            self._stop_music()
            return
        self._stop_music()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(0.3)
            pygame.mixer.music.play(-1)
            self.music_category = category
        except Exception:
            self.music_category = None

    def _stop_music(self):
        if MUSIC_AVAILABLE:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.music_category = None

    def _preview_song(self, song, duration=10):
        if not song or not MUSIC_AVAILABLE:
            return
        path = os.path.join(MUSIC_DIR, song + ".mp3")
        if not os.path.exists(path):
            return
        self._stop_music()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(0.3)
            pygame.mixer.music.play(0)
            self.music_category = "preview"
        except Exception:
            pass

    def _update_music_for_view(self):
        if not self.username:
            self._stop_music()
            return
        if self.current_view == "posts":
            self._play_music("topic")
        elif self.current_view == "topics":
            self._play_music("forum")
        else:
            self._play_music("main_menu")

    def switch_view(self, view_panel):
        self.main_sizer.Clear(delete_windows=False)
        if self.view_panel:
            self.view_panel.Destroy()
        self.view_panel = view_panel
        self.main_sizer.Add(view_panel, 1, wx.EXPAND)
        self.main_panel.Layout()
        view_panel.SetFocus()

    # --- Login View ---

    def show_login(self):
        self.current_view = "login"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(pnl, label="Chatwisp - Login / Register")
        f = title.GetFont(); f.SetPointSize(f.GetPointSize() + 4); f = f.Bold()
        title.SetFont(f)
        sz.Add(title, 0, wx.TOP | wx.LEFT | wx.RIGHT, 25)
        sz.AddSpacer(15)

        gs = wx.FlexGridSizer(2, 2, 8, 15)
        gs.AddGrowableCol(1)

        gs.Add(wx.StaticText(pnl, label="Username:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 20)
        self.login_user = wx.TextCtrl(pnl)
        gs.Add(self.login_user, 0, wx.EXPAND | wx.RIGHT, 20)

        gs.Add(wx.StaticText(pnl, label="Password:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 20)
        self.login_pass = wx.TextCtrl(pnl, style=wx.TE_PASSWORD)
        gs.Add(self.login_pass, 0, wx.EXPAND | wx.RIGHT, 20)

        sz.Add(gs, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 10)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        self.login_btn = wx.Button(pnl, label="Login")
        self.login_btn.Bind(wx.EVT_BUTTON, self.on_login)
        btn_sz.Add(self.login_btn, 0, wx.RIGHT, 8)

        self.register_btn = wx.Button(pnl, label="Register")
        self.register_btn.Bind(wx.EVT_BUTTON, self.on_register)
        btn_sz.Add(self.register_btn, 0, wx.LEFT, 8)

        sz.Add(btn_sz, 0, wx.LEFT, 20)
        sz.AddStretchSpacer()
        pnl.SetSizer(sz)
        self.switch_view(pnl)
        self.login_user.SetFocus()
        self.announce("Login screen. Enter your username and password.")
        self._update_music_for_view()

    def on_login(self, event):
        self._do_auth("login")

    def on_register(self, event):
        username = self.login_user.GetValue().strip()
        password = self.login_pass.GetValue()
        if not username or not password:
            wx.MessageBox("Username and password required", "Error", wx.OK | wx.ICON_ERROR)
            return
        if len(username) < 3:
            wx.MessageBox("Username must be at least 3 characters", "Error", wx.OK | wx.ICON_ERROR)
            return
        if len(password) < 8:
            wx.MessageBox("Password must be at least 8 characters", "Error", wx.OK | wx.ICON_ERROR)
            return
        self._do_auth("register")

    def _do_auth(self, mode):
        username = self.login_user.GetValue().strip()
        password = self.login_pass.GetValue()
        if not username or not password:
            wx.MessageBox("Username and password required", "Error", wx.OK | wx.ICON_ERROR)
            return
        self.login_btn.Disable()
        self.register_btn.Disable()
        self.announce("Connecting to wss://chatwisp.onrender.com...")
        threading.Thread(target=self._ws_connect, args=("wss://chatwisp.onrender.com", username, password, mode), daemon=True).start()

    def _ws_connect(self, uri, username, password, mode):
        try:
            with websockets.sync.client.connect(uri) as ws:
                self.ws = ws
                self.connected = True
                ws.send(json.dumps({"type": mode, "username": username, "password": password, "client_version": VERSION}))
                response = json.loads(ws.recv())
                if response.get("type") == "login_success":
                    self.username = response["username"]
                    self.is_admin = response.get("is_admin", False)
                    self.is_super_admin = response.get("super_admin", False)
                    self.recv_queue.put(("auth_success", response))
                    self._ws_recv_loop(ws)
                elif response.get("type") == "register_success":
                    self.recv_queue.put(("register_ok", response))
                    ws.close()
                else:
                    self.recv_queue.put(("auth_error", response.get("message", "Authentication failed")))
                    ws.close()
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    def _ws_recv_loop(self, ws):
        try:
            for raw in ws:
                data = json.loads(raw)
                self.recv_queue.put(("message", data))
        except Exception:
            if self.running:
                self.recv_queue.put(("disconnected", None))

    def on_poll_recv(self, event):
        try:
            while True:
                msg = self.recv_queue.get_nowait()
                self._handle_recv(msg)
        except queue.Empty:
            pass

    def _handle_recv(self, msg):
        msg_type = msg[0]
        data = msg[1]

        if msg_type == "connection_error":
            wx.CallAfter(self._enable_login_buttons)
            wx.MessageBox(f"Could not connect: {data}", "Connection Error", wx.OK | wx.ICON_ERROR)
            self.announce("Connection failed")
            return

        if msg_type == "auth_error":
            wx.CallAfter(self._enable_login_buttons)
            wx.MessageBox(data, "Login Error", wx.OK | wx.ICON_ERROR)
            self.announce("Authentication failed")
            return

        if msg_type == "register_ok":
            wx.CallAfter(self._enable_login_buttons)
            wx.MessageBox("Registration successful! You can now log in.", "Success", wx.OK | wx.ICON_INFORMATION)
            self.announce("Registration successful")
            return

        if msg_type == "auth_success":
            wx.CallAfter(self._enable_login_buttons)
            self.announce(f"Welcome, {self.username}!")
            self.show_main_menu()
            return

        if msg_type == "disconnected":
            self.connected = False
            wx.MessageBox("Lost connection to server", "Disconnected", wx.OK | wx.ICON_ERROR)
            self.show_login()
            return

        if msg_type == "message":
            self._handle_server_message(data)

    def _enable_login_buttons(self):
        self.login_btn.Enable()
        self.register_btn.Enable()

    def _handle_server_message(self, data):
        dtype = data.get("type")
        if dtype == "welcome":
            wx.MessageBox(data.get("message", ""), "Welcome", wx.OK | wx.ICON_INFORMATION)
            self._send({"type": "get_music_prefs"})
        elif dtype == "forums_list":
            self.show_forums(data["forums"])
        elif dtype == "topics_list":
            self.show_topics(data["forum_id"], data["topics"])
        elif dtype == "posts_list":
            self.current_topic_data = data["topic"]
            self.show_posts(data["topic"], data["posts"])
        elif dtype == "topic_created":
            self.announce("Topic created")
            if self.forum_id_stack: self._request_topics(self.forum_id_stack[-1])
        elif dtype == "post_created":
            self.announce("Post created")
            if self.topic_id_stack: self._request_posts(self.topic_id_stack[-1])
        elif dtype == "forum_created":
            self.announce("Forum created")
            self._request_forums()
        elif dtype == "topic_closed":
            self.announce("Topic closed")
            tid = data["topic_id"]
            if self.topic_id_stack and self.topic_id_stack[-1] == tid:
                self._request_posts(tid)
            elif self.forum_id_stack:
                self._request_topics(self.forum_id_stack[-1])
        elif dtype == "topic_reopened":
            self.announce("Topic reopened")
            tid = data["topic_id"]
            if self.topic_id_stack and self.topic_id_stack[-1] == tid:
                self._request_posts(tid)
            elif self.forum_id_stack:
                self._request_topics(self.forum_id_stack[-1])
        elif dtype == "users_list":
            self.show_users(data["users"])
        elif dtype == "banned":
            self.announce(data.get("message", ""))
        elif dtype == "unbanned":
            self.announce(data.get("message", ""))
        elif dtype == "user_deleted":
            self.announce(data.get("message", ""))
            self._request_users()
        elif dtype == "promoted":
            if data.get("username") == self.username:
                self.is_admin = True
            self.announce(data.get("message", ""))
            wx.MessageBox(data.get("message", ""), "Admin Promotion", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "demoted":
            if data.get("username") == self.username:
                self.is_admin = False
            self.announce(data.get("message", ""))
            wx.MessageBox(data.get("message", ""), "Admin Demotion", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "motd_set":
            self.announce(data.get("message", ""))
            wx.MessageBox(data.get("message", ""), "MOTD Updated", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "unread_dms":
            self.unread_count = data.get("count", 0)
            self.announce(f"You have {self.unread_count} unread message{'s' if self.unread_count != 1 else ''}" if self.unread_count > 0 else "No unread messages")
        elif dtype == "dm_contacts":
            wx.CallAfter(self.show_dm_contacts, data["contacts"])
        elif dtype == "search_results":
            wx.CallAfter(self.show_dm_search_results, data["users"])
        elif dtype == "dm_conversation":
            wx.CallAfter(self.show_dm_conversation, data["messages"])
        elif dtype == "dm_sent":
            self.announce("Message sent")
            if self.dm_contact:
                self._send({"type": "get_dm_conversation", "username": self.dm_contact})
        elif dtype == "dm_received":
            dm = data["dm"]
            other = dm["recipient"] if dm["sender"] == self.username else dm["sender"]
            if self.current_view == "dm_chat" and self.dm_contact == other:
                self._send({"type": "get_dm_conversation", "username": other})
                self._send({"type": "mark_dms_read", "username": other})
            else:
                self.unread_count += 1
                self.announce(f"New message from {other}")
        elif dtype == "post_deleted":
            self.announce("Post deleted")
            if self.topic_id_stack:
                self._request_posts(self.topic_id_stack[-1])
        elif dtype == "topic_deleted":
            self.announce("Topic deleted")
            if self.forum_id_stack:
                self._request_topics(self.forum_id_stack[-1])
        elif dtype == "topic_admin_only_set":
            self.announce("Topic set to admin only")
            if self.topic_id_stack:
                self._request_posts(self.topic_id_stack[-1])
        elif dtype == "topic_admin_only_removed":
            self.announce("Topic no longer admin only")
            if self.topic_id_stack:
                self._request_posts(self.topic_id_stack[-1])
        elif dtype == "password_reset":
            self.announce(data.get("message", ""))
            wx.MessageBox(data.get("message", ""), "Password Reset", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "pong":
            if self._pending_ping_time:
                rtt = int((time.time() - self._pending_ping_time) * 1000)
                self._pending_ping_time = 0
                msg = f"Ping complete. The ping took {rtt} milliseconds."
                self.announce(msg)
                self._tts_speak(msg)
        elif dtype == "server_info":
            if self._pending_server_info:
                self._pending_server_info = False
                uptime = data.get("uptime", 0)
                days = uptime // 86400
                hours = (uptime % 86400) // 3600
                minutes = (uptime % 3600) // 60
                seconds = uptime % 60
                parts = []
                if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
                if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
                parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
                msg = f"Server has been up for " + ", ".join(parts)
                self.announce(msg)
                self._tts_speak(msg)
        elif dtype == "bot_dm_sent":
            self.announce("Message sent as official account")
        elif dtype == "bot_broadcast_complete":
            self.announce(data.get("message", ""))
            wx.MessageBox(data.get("message", ""), "Broadcast Complete", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "bot_post_created":
            self.announce("Post created as official account")
        elif dtype == "bot_topic_created":
            self.announce("Topic created as official account")
            if self.forum_id_stack:
                self._request_topics(self.forum_id_stack[-1])
        elif dtype == "signature_data":
            if hasattr(self, 'sig_text'):
                self.sig_text.SetValue(data.get("signature", ""))
                self._on_sig_text(None)
        elif dtype == "signature_updated":
            self.announce("Signature saved")
            wx.MessageBox("Signature updated", "Settings", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "music_prefs_data":
            self.music_prefs = data.get("prefs", {})
            self._populate_music_lists()
            self._update_music_for_view()
        elif dtype == "music_prefs_updated":
            self.announce("Music preferences saved")
            wx.MessageBox(data.get("message", "Music preferences saved"), "Settings", wx.OK | wx.ICON_INFORMATION)
        elif dtype == "error":
            wx.MessageBox(data.get("message", "Unknown error"), "Error", wx.OK | wx.ICON_ERROR)
            self.announce(f"Error: {data.get('message', '')}")

    def _send(self, msg):
        if not self.connected or not self.ws:
            self.announce("Not connected")
            return
        try:
            self.ws.send(json.dumps(msg))
        except Exception as e:
            self.recv_queue.put(("connection_error", str(e)))

    def _request_forums(self):
        self._send({"type": "get_forums"})

    def _request_topics(self, forum_id):
        self._send({"type": "get_topics", "forum_id": forum_id})

    def _request_posts(self, topic_id):
        self._send({"type": "get_posts", "topic_id": topic_id})

    def _request_users(self):
        self._send({"type": "get_users"})

    # --- Main Menu (Forums) ---

    def show_main_menu(self):
        self.current_topic_data = None
        self.announce("Loading forums...")
        self._request_forums()

    def show_forums(self, forums):
        self.current_view = "forums"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(pnl, label=f"Select Forum  (logged in as {self.username})")
        f = title.GetFont(); f.SetPointSize(f.GetPointSize() + 3); f = f.Bold()
        title.SetFont(f)
        sz.Add(title, 0, wx.ALL, 15)

        msg_btn = wx.Button(pnl, label="Messages")
        msg_btn.Bind(wx.EVT_BUTTON, lambda e: self._send({"type": "get_dm_contacts"}))
        admin_sz = wx.BoxSizer(wx.HORIZONTAL)
        admin_sz.Add(msg_btn, 0, wx.RIGHT, 5)
        settings_btn = wx.Button(pnl, label="Settings")
        settings_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_settings())
        admin_sz.Add(settings_btn, 0, wx.RIGHT, 5)
        if self.is_admin:
            admin_sz.Add(wx.StaticText(pnl, label="  Admin:  "), 0, wx.ALIGN_CENTER_VERTICAL)
            accts_btn = wx.Button(pnl, label="Accounts")
            accts_btn.Bind(wx.EVT_BUTTON, lambda e: self._request_users())
            admin_sz.Add(accts_btn, 0, wx.RIGHT, 5)
            new_forum_btn = wx.Button(pnl, label="New Forum")
            new_forum_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_create_forum_dialog())
            admin_sz.Add(new_forum_btn, 0, wx.LEFT, 5)
            set_motd_btn = wx.Button(pnl, label="Set MOTD")
            set_motd_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_set_motd_dialog())
            admin_sz.Add(set_motd_btn, 0, wx.LEFT, 5)
            bot_btn = wx.Button(pnl, label="Official Account")
            bot_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_bot_controls())
            admin_sz.Add(bot_btn, 0, wx.LEFT, 5)
        sz.Add(admin_sz, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        list_label = wx.StaticText(pnl, label="Forums:")
        sz.Add(list_label, 0, wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(3)

        self.forum_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.forum_ids = []
        for f_data in forums:
            self.forum_list.Append(f"Forum name: {f_data['name']}, description: {f_data['description']}")
            self.forum_ids.append(f_data["id"])
        self.forum_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_forum_select)
        sz.Add(self.forum_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if self.forum_list.GetCount() > 0:
            self.forum_list.SetFocus()
            self.forum_list.SetSelection(0)
        self.announce(f"Forum list loaded. {len(forums)} forums.")
        self._update_music_for_view()

    def _do_forum_select(self):
        idx = self.forum_list.GetSelection()
        if idx >= 0 and idx < len(self.forum_ids):
            fid = self.forum_ids[idx]
            self.forum_id_stack.append(fid)
            self.announce("Loading topics...")
            self._request_topics(fid)

    def on_forum_select(self, event):
        self._do_forum_select()

    # --- Topics View ---

    def show_topics(self, forum_id, topics):
        self.current_view = "topics"
        if not self.forum_id_stack or self.forum_id_stack[-1] != forum_id:
            self.forum_id_stack.append(forum_id)

        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        nav_sz = wx.BoxSizer(wx.HORIZONTAL)
        home_btn = wx.Button(pnl, label="Home")
        home_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_main_menu())
        nav_sz.Add(home_btn, 0, wx.RIGHT, 5)
        new_topic_btn = wx.Button(pnl, label="New Topic")
        new_topic_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_create_topic_dialog())
        nav_sz.Add(new_topic_btn, 0, wx.LEFT, 5)
        sz.Add(nav_sz, 0, wx.ALL, 10)

        if self.is_admin:
            admin_sz = wx.BoxSizer(wx.HORIZONTAL)
            admin_sz.Add(wx.StaticText(pnl, label="Admin:  "), 0, wx.ALIGN_CENTER_VERTICAL)
            close_btn = wx.Button(pnl, label="Close Topic")
            close_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_close_topic())
            admin_sz.Add(close_btn, 0, wx.RIGHT, 5)
            reopen_btn = wx.Button(pnl, label="Reopen Topic")
            reopen_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_reopen_topic())
            admin_sz.Add(reopen_btn, 0, wx.LEFT, 5)
            delete_topic_btn = wx.Button(pnl, label="Delete Topic")
            delete_topic_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_delete_topic())
            admin_sz.Add(delete_topic_btn, 0, wx.LEFT, 5)
            sz.Add(admin_sz, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sz.Add(wx.StaticText(pnl, label="Topics:"), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(3)

        self.topic_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.topic_ids = []
        self.topic_closed = []
        for t in topics:
            status = " [CLOSED]" if t["closed"] else ""
            self.topic_list.Append(f"{t['title']} by {t['author']}{status} ({t['post_count']} posts)")
            self.topic_ids.append(t["id"])
            self.topic_closed.append(t["closed"])
        self.topic_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_topic_select)
        sz.Add(self.topic_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if self.topic_list.GetCount() > 0:
            self.topic_list.SetFocus()
            self.topic_list.SetSelection(0)
        self.announce(f"Topics loaded. {len(topics)} topics.")
        self._update_music_for_view()

    def _do_topic_select(self):
        idx = self.topic_list.GetSelection()
        if idx >= 0 and idx < len(self.topic_ids):
            tid = self.topic_ids[idx]
            self.topic_id_stack.append(tid)
            self.announce("Loading posts...")
            self._request_posts(tid)

    def on_topic_select(self, event):
        self._do_topic_select()

    # --- Posts View ---

    def show_posts(self, topic, posts):
        self.current_view = "posts"
        if not self.topic_id_stack or self.topic_id_stack[-1] != topic["id"]:
            self.topic_id_stack.append(topic["id"])

        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        nav_sz = wx.BoxSizer(wx.HORIZONTAL)
        home_btn = wx.Button(pnl, label="Home")
        home_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_main_menu())
        nav_sz.Add(home_btn, 0, wx.RIGHT, 5)
        topics_btn = wx.Button(pnl, label="Back to Topics")
        topics_btn.Bind(wx.EVT_BUTTON, lambda e: self._go_back_to_topics())
        nav_sz.Add(topics_btn, 0, wx.LEFT, 5)
        copy_link_btn = wx.Button(pnl, label="Copy Topic Link")
        copy_link_btn.Bind(wx.EVT_BUTTON, lambda e: self._copy_topic_link(topic))
        nav_sz.Add(copy_link_btn, 0, wx.LEFT, 5)
        sz.Add(nav_sz, 0, wx.ALL, 10)

        if self.is_admin:
            admin_sz = wx.BoxSizer(wx.HORIZONTAL)
            admin_sz.Add(wx.StaticText(pnl, label="Admin:  "), 0, wx.ALIGN_CENTER_VERTICAL)
            if topic["closed"]:
                reopen_btn = wx.Button(pnl, label="Reopen Topic")
                reopen_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_reopen_topic())
                admin_sz.Add(reopen_btn, 0, wx.RIGHT, 5)
            else:
                close_btn = wx.Button(pnl, label="Close Topic")
                close_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_close_topic())
                admin_sz.Add(close_btn, 0, wx.RIGHT, 5)
            toggle_admin_only_btn = wx.Button(pnl, label="Remove Admin Only" if topic.get("admin_only") else "Make Admin Only")
            toggle_admin_only_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_toggle_admin_only())
            admin_sz.Add(toggle_admin_only_btn, 0, wx.RIGHT, 5)
            delete_post_btn = wx.Button(pnl, label="Delete Selected Post")
            delete_post_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_delete_post())
            admin_sz.Add(delete_post_btn, 0, wx.RIGHT, 5)
            delete_topic_btn = wx.Button(pnl, label="Delete Topic")
            delete_topic_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_delete_topic())
            admin_sz.Add(delete_topic_btn, 0, wx.RIGHT, 5)
            sz.Add(admin_sz, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        status_tags = ""
        if topic["closed"]: status_tags += " [CLOSED]"
        if topic.get("admin_only"): status_tags += " [ADMIN ONLY]"
        title_text = f"Topic: {topic['title']}{status_tags}"
        sz.Add(wx.StaticText(pnl, label=title_text), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(3)

        posts_label = wx.StaticText(pnl, label="Posts in this topic:")
        sz.Add(posts_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sz.AddSpacer(3)

        self.posts_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.posts_data = posts
        for p in posts:
            display = p['content']
            if p.get('signature'):
                display += f"\n— {p['signature']}"
            self.posts_list.Append(f"{p['author']} said: {display}")
        sz.Add(self.posts_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        can_reply = not topic["closed"] and (not topic.get("admin_only") or self.is_admin)
        if can_reply:
            sz.AddSpacer(5)
            reply_label = wx.StaticText(pnl, label="Your reply:")
            sz.Add(reply_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
            self.reply_text = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
            sz.Add(self.reply_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
            send_btn = wx.Button(pnl, label="Send Reply")
            send_btn.Bind(wx.EVT_BUTTON, self.on_send_reply)
            sz.Add(send_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.TOP, 10)
        elif topic.get("admin_only") and not self.is_admin:
            msg = wx.StaticText(pnl, label="This topic is admin only. Only admins can post here.")
            sz.Add(msg, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if can_reply:
            self.reply_text.SetFocus()
        else:
            self.posts_list.SetFocus()
        self.announce(f"Posts loaded. {len(posts)} posts.")
        self._update_music_for_view()

    def on_send_reply(self, event=None):
        content = self.reply_text.GetValue().strip()
        if not content:
            wx.MessageBox("Post content is required", "Error", wx.OK | wx.ICON_ERROR)
            return
        tid = self.topic_id_stack[-1] if self.topic_id_stack else None
        if tid:
            self._send({"type": "create_post", "topic_id": tid, "content": content})
            self.reply_text.SetValue("")
            self.announce("Sending reply...")

    # --- Create Topic Dialog ---

    def show_create_topic_dialog(self, event=None):
        if not self.forum_id_stack:
            return
        dlg = wx.Dialog(self, title="Create New Topic", size=(450, 320))
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(dlg, label="Topic Title:"), 0, wx.TOP | wx.LEFT | wx.RIGHT, 15)
        title_ctrl = wx.TextCtrl(dlg)
        sz.Add(title_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(8)

        sz.Add(wx.StaticText(dlg, label="First Post (optional):"), 0, wx.LEFT | wx.RIGHT, 15)
        content_ctrl = wx.TextCtrl(dlg, style=wx.TE_MULTILINE, size=(-1, 120))
        sz.Add(content_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(10)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(dlg, wx.ID_OK, label="Create")
        cancel_btn = wx.Button(dlg, wx.ID_CANCEL, label="Cancel")
        btn_sz.Add(ok_btn, 0, wx.RIGHT, 8)
        btn_sz.Add(cancel_btn, 0)
        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        dlg.SetSizer(sz)
        title_ctrl.SetFocus()

        if dlg.ShowModal() == wx.ID_OK:
            title = title_ctrl.GetValue().strip()
            content = content_ctrl.GetValue().strip()
            if title:
                fid = self.forum_id_stack[-1]
                self._send({"type": "create_topic", "forum_id": fid, "title": title, "content": content})
                self.announce("Creating topic...")
        dlg.Destroy()

    # --- Create Forum Dialog (Admin) ---

    def show_create_forum_dialog(self, event=None):
        if not self.is_admin:
            return
        dlg = wx.Dialog(self, title="Create New Forum", size=(450, 250))
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(dlg, label="Forum Name:"), 0, wx.TOP | wx.LEFT | wx.RIGHT, 15)
        name_ctrl = wx.TextCtrl(dlg)
        sz.Add(name_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(8)

        sz.Add(wx.StaticText(dlg, label="Description:"), 0, wx.LEFT | wx.RIGHT, 15)
        desc_ctrl = wx.TextCtrl(dlg)
        sz.Add(desc_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(15)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(dlg, wx.ID_OK, label="Create Forum")
        cancel_btn = wx.Button(dlg, wx.ID_CANCEL, label="Cancel")
        btn_sz.Add(ok_btn, 0, wx.RIGHT, 8)
        btn_sz.Add(cancel_btn, 0)
        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        dlg.SetSizer(sz)
        name_ctrl.SetFocus()

        if dlg.ShowModal() == wx.ID_OK:
            name = name_ctrl.GetValue().strip()
            desc = desc_ctrl.GetValue().strip()
            if name:
                self._send({"type": "create_forum", "name": name, "description": desc})
                self.announce("Creating forum...")
        dlg.Destroy()

    # --- Set MOTD Dialog (Admin) ---

    def show_set_motd_dialog(self, event=None):
        if not self.is_admin:
            return
        dlg = wx.Dialog(self, title="Set Message of the Day", size=(450, 200))
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(dlg, label="Message of the Day:"), 0, wx.TOP | wx.LEFT | wx.RIGHT, 15)
        motd_ctrl = wx.TextCtrl(dlg)
        sz.Add(motd_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(15)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(dlg, wx.ID_OK, label="Set MOTD")
        cancel_btn = wx.Button(dlg, wx.ID_CANCEL, label="Cancel")
        btn_sz.Add(ok_btn, 0, wx.RIGHT, 8)
        btn_sz.Add(cancel_btn, 0)
        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        dlg.SetSizer(sz)
        motd_ctrl.SetFocus()

        if dlg.ShowModal() == wx.ID_OK:
            motd = motd_ctrl.GetValue().strip()
            if motd:
                self._send({"type": "set_motd", "motd": motd})
                self.announce("Setting MOTD...")
        dlg.Destroy()

    # --- Accounts View (Admin) ---

    def show_users(self, users):
        self.current_view = "accounts"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        nav_sz = wx.BoxSizer(wx.HORIZONTAL)
        home_btn = wx.Button(pnl, label="Home")
        home_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_main_menu())
        nav_sz.Add(home_btn, 0)
        sz.Add(nav_sz, 0, wx.ALL, 10)

        sz.Add(wx.StaticText(pnl, label="User Accounts:"), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(3)

        self.users_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.users_data = users
        for u in users:
            parts = [f"Username: {u['username']}"]
            if u.get("is_admin"): parts.append("[Admin]")
            if u.get("banned"):
                reason = u.get("ban_reason") or "No reason"
                parts.append(f"[Banned: {reason}]")
            self.users_list.Append(" ".join(parts))
        self.users_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_user_select)
        sz.Add(self.users_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if self.users_list.GetCount() > 0:
            self.users_list.SetFocus()
            self.users_list.SetSelection(0)
        self.announce(f"Users loaded. {len(users)} users.")

    def _do_user_select(self):
        idx = self.users_list.GetSelection()
        if idx >= 0 and idx < len(self.users_data):
            self.show_user_detail_dialog(self.users_data[idx])

    def on_user_select(self, event):
        self._do_user_select()

    def show_user_detail_dialog(self, user):
        dlg = wx.Dialog(self, title=f"User: {user['username']}", size=(400, 340))
        sz = wx.BoxSizer(wx.VERTICAL)

        info = f"Username: {user['username']}\nAdmin: {'Yes' if user.get('is_admin') else 'No'}\nBanned: {'Yes' if user.get('banned') else 'No'}"
        if user.get("ban_reason"):
            info += f"\nBan Reason: {user['ban_reason']}"
        sz.Add(wx.StaticText(dlg, label=info), 0, wx.ALL, 15)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)

        if not user.get("banned"):
            ban_btn = wx.Button(dlg, label="Ban User")
            ban_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_ban_flow(dlg, user))
            btn_sz.Add(ban_btn, 0, wx.RIGHT, 5)
        else:
            unban_btn = wx.Button(dlg, label="Unban User")
            unban_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_simple_action(dlg, "unban_user", user["username"]))
            btn_sz.Add(unban_btn, 0, wx.RIGHT, 5)

        if user["username"] != self.username:
            delete_btn = wx.Button(dlg, label="Delete User")
            delete_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_delete_user(dlg, user))
            btn_sz.Add(delete_btn, 0, wx.RIGHT, 5)

        if self.is_super_admin and not user.get("super_admin") and user["username"] != self.username:
            if not user.get("is_admin"):
                promote_btn = wx.Button(dlg, label="Promote to Admin")
                promote_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_simple_action(dlg, "promote_admin", user["username"]))
                btn_sz.Add(promote_btn, 0, wx.RIGHT, 5)
            elif user.get("is_admin") and not user.get("super_admin"):
                demote_btn = wx.Button(dlg, label="Demote from Admin")
                demote_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_simple_action(dlg, "demote_admin", user["username"]))
                btn_sz.Add(demote_btn, 0, wx.RIGHT, 5)

        if self.is_admin and user["username"] != self.username:
            reset_pw_btn = wx.Button(dlg, label="Reset Password")
            reset_pw_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_reset_password(dlg, user))
            btn_sz.Add(reset_pw_btn, 0, wx.RIGHT, 5)

        close_btn = wx.Button(dlg, wx.ID_CLOSE, label="Close")
        btn_sz.Add(close_btn, 0)

        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)
        dlg.SetSizer(sz)
        dlg.ShowModal()
        dlg.Destroy()

    def _do_simple_action(self, dlg, action, username):
        self._send({"type": action, "username": username})
        dlg.EndModal(wx.ID_CLOSE)
        labels = {"ban_user": "Banning", "unban_user": "Unbanning", "promote_admin": "Promoting", "demote_admin": "Demoting", "delete_user": "Deleting"}
        self.announce(f"{labels.get(action, 'Processing')} {username}...")

    def _do_ban_flow(self, parent_dlg, user):
        parent_dlg.EndModal(wx.ID_CLOSE)
        dlg = wx.Dialog(self, title=f"Ban {user['username']}", size=(400, 250))
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(dlg, label="Ban Reason (optional):"), 0, wx.TOP | wx.LEFT | wx.RIGHT, 15)
        reason_ctrl = wx.TextCtrl(dlg)
        sz.Add(reason_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(8)

        sz.Add(wx.StaticText(dlg, label="Duration (optional, leave blank for infinite):"), 0, wx.LEFT | wx.RIGHT, 15)
        duration_ctrl = wx.TextCtrl(dlg)
        sz.Add(duration_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(15)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(dlg, wx.ID_OK, label="Ban")
        cancel_btn = wx.Button(dlg, wx.ID_CANCEL, label="Cancel")
        btn_sz.Add(ok_btn, 0, wx.RIGHT, 8)
        btn_sz.Add(cancel_btn, 0)
        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        dlg.SetSizer(sz)
        reason_ctrl.SetFocus()

        if dlg.ShowModal() == wx.ID_OK:
            reason = reason_ctrl.GetValue().strip() or None
            duration = duration_ctrl.GetValue().strip() or None
            self._send({"type": "ban_user", "username": user["username"], "reason": reason, "duration": duration})
            self.announce(f"Banning {user['username']}...")
        dlg.Destroy()

    def _do_delete_user(self, parent_dlg, user):
        result = wx.MessageBox(
            f"Are you sure you want to delete user '{user['username']}'?\n\nThis action cannot be undone.",
            "Confirm Delete",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if result == wx.YES:
            self._send({"type": "delete_user", "username": user["username"]})
            parent_dlg.EndModal(wx.ID_CLOSE)
            self.announce(f"Deleting {user['username']}...")

    def _do_reset_password(self, parent_dlg, user):
        dlg = wx.Dialog(self, title=f"Reset Password for {user['username']}", size=(400, 250))
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(dlg, label="New Password:"), 0, wx.TOP | wx.LEFT | wx.RIGHT, 15)
        new_pw = wx.TextCtrl(dlg, style=wx.TE_PASSWORD)
        sz.Add(new_pw, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(8)

        sz.Add(wx.StaticText(dlg, label="Confirm Password:"), 0, wx.LEFT | wx.RIGHT, 15)
        confirm_pw = wx.TextCtrl(dlg, style=wx.TE_PASSWORD)
        sz.Add(confirm_pw, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(15)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(dlg, wx.ID_OK, label="Reset Password")
        cancel_btn = wx.Button(dlg, wx.ID_CANCEL, label="Cancel")
        btn_sz.Add(ok_btn, 0, wx.RIGHT, 8)
        btn_sz.Add(cancel_btn, 0)
        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        dlg.SetSizer(sz)
        new_pw.SetFocus()

        if dlg.ShowModal() == wx.ID_OK:
            p1 = new_pw.GetValue()
            p2 = confirm_pw.GetValue()
            if not p1 or len(p1) < 8:
                wx.MessageBox("Password must be at least 8 characters", "Error", wx.OK | wx.ICON_ERROR)
            elif p1 != p2:
                wx.MessageBox("Passwords do not match", "Error", wx.OK | wx.ICON_ERROR)
            else:
                self._send({"type": "reset_password", "username": user["username"], "new_password": p1})
                parent_dlg.EndModal(wx.ID_CLOSE)
                self.announce(f"Resetting password for {user['username']}...")
        dlg.Destroy()

    # --- Admin Topic Actions ---

    def _admin_close_topic(self):
        if not self.is_admin:
            self.announce("Admin access required")
            return
        tid = self.topic_id_stack[-1] if self.topic_id_stack else None
        if tid:
            self._send({"type": "close_topic", "topic_id": tid})
            self.announce("Closing topic...")

    def _admin_reopen_topic(self):
        if not self.is_admin:
            self.announce("Admin access required")
            return
        tid = self.topic_id_stack[-1] if self.topic_id_stack else None
        if tid:
            self._send({"type": "reopen_topic", "topic_id": tid})
            self.announce("Reopening topic...")

    def _admin_delete_post(self):
        if not self.is_admin:
            self.announce("Admin access required")
            return
        idx = self.posts_list.GetSelection()
        if idx < 0 or idx >= len(self.posts_data):
            self.announce("Select a post first")
            return
        post = self.posts_data[idx]
        if wx.MessageBox(f"Delete this post by {post['author']}? This cannot be undone.", "Confirm Delete", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            self._send({"type": "delete_post", "post_id": post["id"]})
            self.announce("Deleting post...")

    def _admin_delete_topic(self):
        if not self.is_admin:
            self.announce("Admin access required")
            return
        tid = self.topic_id_stack[-1] if self.topic_id_stack else None
        if tid:
            if wx.MessageBox("Delete this entire topic and all its posts? This cannot be undone.", "Confirm Delete", wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                self._send({"type": "delete_topic", "topic_id": tid})
                self.announce("Deleting topic...")

    def _admin_toggle_admin_only(self):
        if not self.is_admin or not self.topic_id_stack:
            return
        tid = self.topic_id_stack[-1]
        if self.current_topic_data and self.current_topic_data.get("admin_only"):
            self._send({"type": "remove_topic_admin_only", "topic_id": tid})
            self.announce("Removing admin only...")
        else:
            self._send({"type": "set_topic_admin_only", "topic_id": tid})
            self.announce("Setting admin only...")

    def show_bot_controls(self):
        self.current_view = "bot_controls"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(pnl, label="Official Account Controls")
        f = title.GetFont(); f.SetPointSize(f.GetPointSize() + 3); f = f.Bold()
        title.SetFont(f)
        sz.Add(title, 0, wx.ALL, 15)

        # Send DM
        sz.Add(wx.StaticText(pnl, label="Send DM as Official Account:"), 0, wx.LEFT | wx.RIGHT, 10)
        gs = wx.FlexGridSizer(2, 2, 5, 10)
        gs.Add(wx.StaticText(pnl, label="Recipient:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_dm_recipient = wx.TextCtrl(pnl)
        gs.Add(self.bot_dm_recipient, 0, wx.EXPAND)
        gs.Add(wx.StaticText(pnl, label="Message:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_dm_content = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
        gs.Add(self.bot_dm_content, 0, wx.EXPAND)
        gs.AddGrowableCol(1)
        sz.Add(gs, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        send_dm_btn = wx.Button(pnl, label="Send DM")
        send_dm_btn.Bind(wx.EVT_BUTTON, self._on_bot_send_dm)
        sz.Add(send_dm_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sz.AddSpacer(10)
        # Broadcast
        sz.Add(wx.StaticText(pnl, label="Broadcast to All Users:"), 0, wx.LEFT | wx.RIGHT, 10)
        self.bot_broadcast_content = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
        sz.Add(self.bot_broadcast_content, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        broadcast_btn = wx.Button(pnl, label="Broadcast")
        broadcast_btn.Bind(wx.EVT_BUTTON, self._on_bot_broadcast)
        sz.Add(broadcast_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sz.AddSpacer(10)
        # Create Post
        sz.Add(wx.StaticText(pnl, label="Create Post as Official Account:"), 0, wx.LEFT | wx.RIGHT, 10)
        gs2 = wx.FlexGridSizer(2, 2, 5, 10)
        gs2.Add(wx.StaticText(pnl, label="Topic ID:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_post_topic = wx.TextCtrl(pnl)
        gs2.Add(self.bot_post_topic, 0, wx.EXPAND)
        gs2.Add(wx.StaticText(pnl, label="Content:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_post_content = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
        gs2.Add(self.bot_post_content, 0, wx.EXPAND)
        gs2.AddGrowableCol(1)
        sz.Add(gs2, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        create_post_btn = wx.Button(pnl, label="Create Post")
        create_post_btn.Bind(wx.EVT_BUTTON, self._on_bot_create_post)
        sz.Add(create_post_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sz.AddSpacer(10)
        # Create Topic
        sz.Add(wx.StaticText(pnl, label="Create Topic as Official Account:"), 0, wx.LEFT | wx.RIGHT, 10)
        gs3 = wx.FlexGridSizer(3, 2, 5, 10)
        gs3.Add(wx.StaticText(pnl, label="Forum ID:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_topic_forum = wx.TextCtrl(pnl)
        gs3.Add(self.bot_topic_forum, 0, wx.EXPAND)
        gs3.Add(wx.StaticText(pnl, label="Title:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_topic_title = wx.TextCtrl(pnl)
        gs3.Add(self.bot_topic_title, 0, wx.EXPAND)
        gs3.Add(wx.StaticText(pnl, label="Content:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.bot_topic_content = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
        gs3.Add(self.bot_topic_content, 0, wx.EXPAND)
        gs3.AddGrowableCol(1)
        sz.Add(gs3, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        create_topic_btn = wx.Button(pnl, label="Create Topic")
        create_topic_btn.Bind(wx.EVT_BUTTON, self._on_bot_create_topic)
        sz.Add(create_topic_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sz.AddSpacer(10)
        back_btn = wx.Button(pnl, label="Back to Main Menu")
        back_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_main_menu())
        sz.Add(back_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        self.bot_dm_recipient.SetFocus()
        self.announce("Official account controls")

    def _on_bot_send_dm(self, event):
        recipient = self.bot_dm_recipient.GetValue().strip()
        content = self.bot_dm_content.GetValue().strip()
        if not recipient or not content:
            wx.MessageBox("Recipient and content required", "Error", wx.OK | wx.ICON_ERROR)
            return
        self._send({"type": "bot_send_dm", "recipient": recipient, "content": content})
        self.announce("Sending DM as official account...")

    def _on_bot_broadcast(self, event):
        content = self.bot_broadcast_content.GetValue().strip()
        if not content:
            wx.MessageBox("Content required", "Error", wx.OK | wx.ICON_ERROR)
            return
        result = wx.MessageBox("Broadcast this message to ALL users? This cannot be undone.", "Confirm Broadcast", wx.YES_NO | wx.ICON_QUESTION)
        if result == wx.YES:
            self._send({"type": "bot_broadcast", "content": content})
            self.announce("Broadcasting...")

    def _on_bot_create_post(self, event):
        topic_id = self.bot_post_topic.GetValue().strip()
        content = self.bot_post_content.GetValue().strip()
        if not topic_id or not content:
            wx.MessageBox("Topic ID and content required", "Error", wx.OK | wx.ICON_ERROR)
            return
        self._send({"type": "bot_create_post", "topic_id": topic_id, "content": content})
        self.announce("Creating post as official account...")

    def _on_bot_create_topic(self, event):
        forum_id = self.bot_topic_forum.GetValue().strip()
        title = self.bot_topic_title.GetValue().strip()
        content = self.bot_topic_content.GetValue().strip()
        if not forum_id or not title:
            wx.MessageBox("Forum ID and title required", "Error", wx.OK | wx.ICON_ERROR)
            return
        self._send({"type": "bot_create_topic", "forum_id": forum_id, "title": title, "content": content})
        self.announce("Creating topic as official account...")

    def _copy_topic_link(self, topic):
        forum_id = topic.get("forum_id", "")
        slug = topic.get("slug", "")
        if not forum_id or not slug:
            wx.MessageBox("Topic link not available", "Error", wx.OK | wx.ICON_ERROR)
            return
        web_url = f"https://chatwisp.onrender.com/forums/{forum_id}/{slug}"
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(web_url))
            wx.TheClipboard.Close()
            self.announce("Topic link copied to clipboard")
        else:
            wx.MessageBox("Could not copy to clipboard", "Error", wx.OK | wx.ICON_ERROR)

    def show_settings(self):
        self.current_view = "settings"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(pnl, label="Settings")
        f = title.GetFont(); f.SetPointSize(f.GetPointSize() + 3); f = f.Bold()
        title.SetFont(f)
        sz.Add(title, 0, wx.ALL, 15)

        sz.Add(wx.StaticText(pnl, label="Forum Signature:"), 0, wx.LEFT | wx.RIGHT, 10)
        sz.Add(wx.StaticText(pnl, label="This text will appear at the end of every post you make. Max 50 characters."), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(5)
        self.sig_text = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
        self.sig_text.SetMaxLength(50)
        sz.Add(self.sig_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        self.sig_counter = wx.StaticText(pnl, label="0/50")
        sz.Add(self.sig_counter, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        self.sig_text.Bind(wx.EVT_TEXT, self._on_sig_text)
        sz.AddSpacer(10)
        save_btn = wx.Button(pnl, label="Save Signature")
        save_btn.Bind(wx.EVT_BUTTON, self._on_save_signature)
        sz.Add(save_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sz.Add(wx.StaticLine(pnl), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(10)
        music_title = wx.StaticText(pnl, label="Menu Music")
        f2 = music_title.GetFont(); f2 = f2.Bold()
        music_title.SetFont(f2)
        sz.Add(music_title, 0, wx.LEFT | wx.RIGHT, 10)
        credit = wx.StaticText(pnl, label="Music credits: no-copyright-music.com/relaxing/")
        credit.SetFont(credit.GetFont().Italic())
        sz.Add(credit, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        self.music_main_list = wx.ListBox(pnl, style=wx.LB_SINGLE, size=(-1, 80))
        self.music_forum_list = wx.ListBox(pnl, style=wx.LB_SINGLE, size=(-1, 80))
        self.music_topic_list = wx.ListBox(pnl, style=wx.LB_SINGLE, size=(-1, 80))

        for lst, label in [(self.music_main_list, "Main Menu Music"), (self.music_forum_list, "Forum Music"), (self.music_topic_list, "Topic Music")]:
            lst_sz = wx.BoxSizer(wx.VERTICAL)
            lst_sz.Add(wx.StaticText(pnl, label=label), 0, wx.BOTTOM, 3)
            lst.Bind(wx.EVT_KEY_DOWN, self._on_music_list_key)
            lst_sz.Add(lst, 0, wx.EXPAND)
            sz.Add(lst_sz, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        sz.AddSpacer(5)
        save_music_btn = wx.Button(pnl, label="Save Music Settings")
        save_music_btn.Bind(wx.EVT_BUTTON, self._on_save_music_prefs)
        sz.Add(save_music_btn, 0, wx.LEFT | wx.RIGHT, 10)

        sz.AddStretchSpacer()
        back_btn = wx.Button(pnl, label="Back to Main Menu")
        back_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_main_menu())
        sz.Add(back_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        self.sig_text.SetFocus()

        self._send({"type": "get_signature"})
        self._send({"type": "get_music_prefs"})
        self.announce("Settings")
        self._update_music_for_view()

    def _on_sig_text(self, event):
        length = len(self.sig_text.GetValue())
        self.sig_counter.SetLabel(f"{length}/50")

    def _on_save_signature(self, event):
        sig = self.sig_text.GetValue().strip()
        if len(sig) > 50:
            wx.MessageBox("Signature must be 50 characters or less", "Error", wx.OK | wx.ICON_ERROR)
            return
        self._send({"type": "set_signature", "signature": sig})
        self.announce("Saving signature...")

    def _populate_music_lists(self):
        prefs = self.music_prefs
        none_label = "(None)"
        songs = [none_label] + AVAILABLE_SONGS
        for lst, key in [(self.music_main_list, "main_menu"), (self.music_forum_list, "forum"), (self.music_topic_list, "topic")]:
            lst.Clear()
            for s in songs:
                lst.Append(s)
            val = prefs.get(key, "")
            idx = songs.index(val) if val in songs else 0
            lst.SetSelection(idx)
            lst.SetClientData(idx, val)

    def _on_music_list_key(self, event):
        lst = event.GetEventObject()
        key = event.GetKeyCode()
        if key == wx.WXK_SPACE:
            sel = lst.GetSelection()
            if sel >= 0:
                song = lst.GetString(sel)
                if song and song != "(None)":
                    self._preview_song(song)
        event.Skip()

    def _on_save_music_prefs(self, event):
        songs = [""] + AVAILABLE_SONGS
        def get_val(lst):
            sel = lst.GetSelection()
            return lst.GetString(sel) if sel >= 0 else ""
        prefs = {
            "main_menu": get_val(self.music_main_list),
            "forum": get_val(self.music_forum_list),
            "topic": get_val(self.music_topic_list),
        }
        for k in prefs:
            if prefs[k] == "(None)":
                prefs[k] = ""
        self._send({"type": "set_music_prefs", "prefs": prefs})
        self.announce("Saving music preferences...")

    # --- Private Messages ---

    def show_dm_contacts(self, contacts):
        self.current_view = "dm_list"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        nav_sz = wx.BoxSizer(wx.HORIZONTAL)
        home_btn = wx.Button(pnl, label="Home")
        home_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_main_menu())
        nav_sz.Add(home_btn, 0, wx.RIGHT, 5)
        new_msg_btn = wx.Button(pnl, label="New Message")
        new_msg_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_dm_search_dialog())
        nav_sz.Add(new_msg_btn, 0)
        sz.Add(nav_sz, 0, wx.ALL, 10)

        sz.Add(wx.StaticText(pnl, label="Conversations:"), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(3)

        self.dm_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.dm_contacts_data = contacts
        for c in contacts:
            self.dm_list.Append(f"{c['username']}: {c['last_message']}")
        self.dm_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_dm_contact_select)
        sz.Add(self.dm_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if self.dm_list.GetCount() > 0:
            self.dm_list.SetFocus()
            self.dm_list.SetSelection(0)
        self.announce(f"{len(contacts)} conversations.")
        self._update_music_for_view()

    def on_dm_contact_select(self, event):
        idx = self.dm_list.GetSelection()
        if idx >= 0 and idx < len(self.dm_contacts_data):
            self.dm_contact = self.dm_contacts_data[idx]["username"]
            self.show_dm_chat()

    def show_dm_search_dialog(self):
        dlg = wx.Dialog(self, title="Search Users", size=(400, 400))
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(dlg, label="Search for a user:"), 0, wx.TOP | wx.LEFT | wx.RIGHT, 15)
        search_input = wx.TextCtrl(dlg)
        sz.Add(search_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(8)

        results_list = wx.ListBox(dlg, style=wx.LB_SINGLE, size=(-1, 200))
        sz.Add(results_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        sz.AddSpacer(10)

        btn_sz = wx.BoxSizer(wx.HORIZONTAL)
        open_btn = wx.Button(dlg, label="Open Chat")
        cancel_btn = wx.Button(dlg, wx.ID_CANCEL, label="Cancel")
        btn_sz.Add(open_btn, 0, wx.RIGHT, 8)
        btn_sz.Add(cancel_btn, 0)
        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        dlg.SetSizer(sz)
        search_input.SetFocus()

        def on_search(event=None):
            q = search_input.GetValue().strip()
            if q:
                self._send({"type": "search_users", "query": q})
                self.announce("Searching...")

        search_input.Bind(wx.EVT_TEXT, on_search)

        # Temporary handler for search results
        original_recv = self.recv_queue.get

        def on_open(event):
            idx = results_list.GetSelection()
            if idx >= 0:
                username_str = results_list.GetString(idx).split(" (")[0]
                self.dm_contact = username_str
                dlg.EndModal(wx.ID_OK)

        open_btn.Bind(wx.EVT_BUTTON, on_open)
        results_list.Bind(wx.EVT_LISTBOX_DCLICK, on_open)

        # Store results callback
        self._dm_search_list = results_list

        if dlg.ShowModal() == wx.ID_OK and self.dm_contact:
            self.show_dm_chat()
        dlg.Destroy()

    def show_dm_search_results(self, users):
        if hasattr(self, '_dm_search_list') and self._dm_search_list:
            self._dm_search_list.Clear()
            for u in users:
                self._dm_search_list.Append(u)

    def show_dm_chat(self):
        if not self.dm_contact:
            return
        self.current_view = "dm_chat"
        pnl = wx.Panel(self.main_panel)
        sz = wx.BoxSizer(wx.VERTICAL)

        nav_sz = wx.BoxSizer(wx.HORIZONTAL)
        back_btn = wx.Button(pnl, label="Back to Messages")
        back_btn.Bind(wx.EVT_BUTTON, lambda e: self._send({"type": "get_dm_contacts"}))
        nav_sz.Add(back_btn, 0)
        sz.Add(nav_sz, 0, wx.ALL, 10)

        title_text = f"Chat with {self.dm_contact}"
        sz.Add(wx.StaticText(pnl, label=title_text), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(5)

        self.dm_message_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.dm_messages_data = []
        sz.Add(self.dm_message_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sz.AddSpacer(5)
        if self.dm_contact == "Chatwisp Official Account":
            bot_label = wx.StaticText(pnl, label="This is the Chatwisp Official Account. You cannot reply to it.")
            f = bot_label.GetFont(); f = f.Italic()
            bot_label.SetFont(f)
            sz.Add(bot_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        else:
            dm_input_label = wx.StaticText(pnl, label="Type a message:")
            sz.Add(dm_input_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
            self.dm_input = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
            sz.Add(self.dm_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
            send_dm_btn = wx.Button(pnl, label="Send")
            send_dm_btn.Bind(wx.EVT_BUTTON, self.on_send_dm)
            sz.Add(send_dm_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.TOP, 10)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if hasattr(self, 'dm_input') and self.dm_input:
            self.dm_input.SetFocus()

        self._send({"type": "get_dm_conversation", "username": self.dm_contact})
        self._send({"type": "mark_dms_read", "username": self.dm_contact})
        self.announce(f"Chat with {self.dm_contact}")
        self._update_music_for_view()

    def show_dm_conversation(self, messages):
        if not hasattr(self, 'dm_message_list'):
            return
        self.dm_messages_data = messages
        self.dm_message_list.Clear()
        for m in messages:
            label = "You" if m["sender"] == self.username else m["sender"]
            self.dm_message_list.Append(f"{label}: {m['content']}")
        if messages:
            self.dm_message_list.SetSelection(len(messages) - 1)
        self.announce(f"{len(messages)} messages.")

    def on_send_dm(self, event=None):
        content = self.dm_input.GetValue().strip()
        if not content or not self.dm_contact:
            return
        self._send({"type": "send_dm", "recipient": self.dm_contact, "content": content})
        self.dm_input.SetValue("")
        self.announce("Sending message...")

    # --- Navigation ---

    def _go_back_to_topics(self):
        if self.forum_id_stack:
            fid = self.forum_id_stack[-1]
            if self.topic_id_stack:
                self.topic_id_stack.pop()
            self._request_topics(fid)

    def _go_back_to_forums(self):
        if self.forum_id_stack:
            self.forum_id_stack.pop()
        self._request_forums()

    def on_char_hook(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_RETURN:
            focused = wx.Window.FindFocus()
            if focused == getattr(self, 'forum_list', None) and hasattr(self, 'forum_list'):
                self._do_forum_select()
                return
            if focused == getattr(self, 'topic_list', None) and hasattr(self, 'topic_list'):
                self._do_topic_select()
                return
            if focused == getattr(self, 'users_list', None) and hasattr(self, 'users_list'):
                self._do_user_select()
                return
            if focused == getattr(self, 'dm_list', None) and hasattr(self, 'dm_list'):
                self.on_dm_contact_select(None)
                return
        elif key == wx.WXK_ESCAPE:
            if self.current_view == "login":
                self.Close()
            elif self.current_view == "forums":
                self.Close()
            elif self.current_view == "topics":
                self.show_main_menu()
            elif self.current_view == "posts":
                self._go_back_to_topics()
            elif self.current_view == "accounts":
                self.show_main_menu()
            else:
                self.show_main_menu()
            return
        elif key == wx.WXK_F1:
            self._tts_speak("Retrieving server info")
            self._pending_server_info = True
            self._send({"type": "server_info"})
            return
        elif key == wx.WXK_F2:
            self._tts_speak("Pinging...")
            self._pending_ping_time = time.time()
            self._send({"type": "ping", "client_time": self._pending_ping_time})
            return
        event.Skip()

    def on_ctrl_n(self, event):
        if self.current_view == "forums" and self.is_admin:
            self.show_create_forum_dialog()
        elif self.current_view == "topics":
            self.show_create_topic_dialog()

    def on_ctrl_k(self, event):
        if self.is_admin and self.current_view in ("posts", "topics"):
            self._admin_close_topic()

    def on_ctrl_o(self, event):
        if self.is_admin and self.current_view in ("posts", "topics"):
            self._admin_reopen_topic()

    def on_close(self, event):
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.Destroy()


class ChatwispApp(wx.App):
    def OnInit(self):
        self.frame = ChatwispFrame()
        self.frame.Show()
        return True


def main():
    if not HAS_WEBSOCKETS:
        wx.MessageBox(
            "websockets library not found.\nRun: pip install websockets",
            "Error",
            wx.OK | wx.ICON_ERROR,
        )
        return
    app = ChatwispApp()
    app.MainLoop()


if __name__ == "__main__":
    main()
