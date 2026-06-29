#!/usr/bin/env python3
import wx
import json
import threading
import queue
import sys
import os

try:
    import websockets.sync.client
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


class ChatwispFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Chatwisp", size=(800, 600))
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
        self.forum_id_stack = []
        self.topic_id_stack = []
        self.current_view = None

        self.recv_queue = queue.Queue()
        self.send_queue = queue.Queue()
        self.running = True

        self.view_panel = None

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
        if len(password) < 4:
            wx.MessageBox("Password must be at least 4 characters", "Error", wx.OK | wx.ICON_ERROR)
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
        self.announce("Connecting to ws://127.0.0.1:8765...")
        threading.Thread(target=self._ws_connect, args=("ws://127.0.0.1:8765", username, password, mode), daemon=True).start()

    def _ws_connect(self, uri, username, password, mode):
        try:
            with websockets.sync.client.connect(uri) as ws:
                self.ws = ws
                self.connected = True
                ws.send(json.dumps({"type": mode, "username": username, "password": password}))
                response = json.loads(ws.recv())
                if response.get("type") == "login_success":
                    self.username = response["username"]
                    self.is_admin = response.get("is_admin", False)
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
        if dtype == "forums_list":
            self.show_forums(data["forums"])
        elif dtype == "topics_list":
            self.show_topics(data["forum_id"], data["topics"])
        elif dtype == "posts_list":
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

        if self.is_admin:
            admin_sz = wx.BoxSizer(wx.HORIZONTAL)
            admin_sz.Add(wx.StaticText(pnl, label="Admin Controls:  "), 0, wx.ALIGN_CENTER_VERTICAL)
            accts_btn = wx.Button(pnl, label="Accounts")
            accts_btn.Bind(wx.EVT_BUTTON, lambda e: self._request_users())
            admin_sz.Add(accts_btn, 0, wx.RIGHT, 5)
            new_forum_btn = wx.Button(pnl, label="New Forum")
            new_forum_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_create_forum_dialog())
            admin_sz.Add(new_forum_btn, 0, wx.LEFT, 5)
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
        sz.Add(nav_sz, 0, wx.ALL, 10)

        if self.is_admin:
            admin_sz = wx.BoxSizer(wx.HORIZONTAL)
            admin_sz.Add(wx.StaticText(pnl, label="Admin:  "), 0, wx.ALIGN_CENTER_VERTICAL)
            if topic["closed"]:
                reopen_btn = wx.Button(pnl, label="Reopen Topic")
                reopen_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_reopen_topic())
                admin_sz.Add(reopen_btn, 0)
            else:
                close_btn = wx.Button(pnl, label="Close Topic")
                close_btn.Bind(wx.EVT_BUTTON, lambda e: self._admin_close_topic())
                admin_sz.Add(close_btn, 0)
            sz.Add(admin_sz, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        title_text = f"Topic: {topic['title']}{' [CLOSED]' if topic['closed'] else ''}"
        sz.Add(wx.StaticText(pnl, label=title_text), 0, wx.LEFT | wx.RIGHT, 10)
        sz.AddSpacer(3)

        posts_label = wx.StaticText(pnl, label="Posts in this topic:")
        sz.Add(posts_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sz.AddSpacer(3)

        self.posts_list = wx.ListBox(pnl, style=wx.LB_SINGLE)
        self.posts_data = posts
        for p in posts:
            self.posts_list.Append(f"{p['author']} said: {p['content']}")
        sz.Add(self.posts_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        if not topic["closed"]:
            sz.AddSpacer(5)
            reply_label = wx.StaticText(pnl, label="Your reply:")
            sz.Add(reply_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
            self.reply_text = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 60))
            sz.Add(self.reply_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
            send_btn = wx.Button(pnl, label="Send Reply")
            send_btn.Bind(wx.EVT_BUTTON, self.on_send_reply)
            sz.Add(send_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.TOP, 10)

        pnl.SetSizer(sz)
        self.switch_view(pnl)
        if not topic["closed"]:
            self.reply_text.SetFocus()
        else:
            self.posts_list.SetFocus()
        self.announce(f"Posts loaded. {len(posts)} posts.")

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
        dlg = wx.Dialog(self, title=f"User: {user['username']}", size=(400, 300))
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

        delete_btn = wx.Button(dlg, label="Delete User")
        delete_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_delete_user(dlg, user))
        btn_sz.Add(delete_btn, 0, wx.RIGHT, 5)

        close_btn = wx.Button(dlg, wx.ID_CLOSE, label="Close")
        btn_sz.Add(close_btn, 0)

        sz.Add(btn_sz, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)
        dlg.SetSizer(sz)
        ban_btn = btn_sz.GetChildren()[0].GetWindow() if not user.get("banned") else None
        if ban_btn:
            ban_btn.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()

    def _do_simple_action(self, dlg, action, username):
        self._send({"type": action, "username": username})
        dlg.EndModal(wx.ID_CLOSE)
        self.announce(f"{'Banning' if action == 'ban_user' else 'Unbanning'} {username}...")

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
