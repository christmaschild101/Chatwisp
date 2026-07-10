let ws = null;
let username = null;
let isAdmin = false;
let isSuperAdmin = false;
let currentForumId = null;
let currentTopicId = null;
let forumsData = [];
let topicsData = [];
let currentTopicData = null;
let dmContacts = [];
let dmCurrentUser = null;
let unreadCount = 0;
let pendingLink = null;
let currentTopicSlug = null;
let pendingLinkTopicId = null;

let savedWsUrl = null;
let savedUser = null;
let savedPass = null;
let savedMode = null;
let authenticated = false;
let reconnectAttempts = 0;
const maxReconnectAttempts = 20;
let keepaliveInterval = null;

let musicPrefs = {};
let currentMusicCategory = null;
let musicPlayer = null;
let _audioUnlocked = false;
let _pendingPlayCategory = null;

function _initAudio() {
  if (_audioUnlocked) return;
  _audioUnlocked = true;
  try {
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    ctx.resume();
  } catch(e) {}
  var silent = new Audio();
  silent.play().catch(function(){});
  if (_pendingPlayCategory) {
    var cat = _pendingPlayCategory;
    _pendingPlayCategory = null;
    playMusic(cat);
  }
}
document.addEventListener('click', _initAudio, { once: true });
document.addEventListener('keydown', _initAudio, { once: true });

function $(id) { return document.getElementById(id); }

function announce(msg) {
  const status = $('status-bar');
  status.textContent = msg;
}

let _isMobile = false;
function updateIsMobile() {
  _isMobile = window.innerWidth < 768;
}
updateIsMobile();
window.addEventListener('resize', updateIsMobile);

function playMusic(category) {
  if (!_audioUnlocked) {
    _pendingPlayCategory = category;
    return;
  }
  var song = musicPrefs[category];
  if (song === undefined) song = 'ByTheFire';
  if (!song) { stopMusic(); return; }
  if (currentMusicCategory === category && musicPlayer && !musicPlayer.paused) return;
  stopMusic();
  var audio = new Audio('/music/' + encodeURIComponent(song) + '.mp3');
  audio.loop = true;
  audio.volume = 0.3;
  audio.play().catch(function(e) {
    announce('Music playback failed: ' + (e.message || 'autoplay blocked'));
  });
  musicPlayer = audio;
  currentMusicCategory = category;
}

function stopMusic() {
  if (musicPlayer) {
    musicPlayer.pause();
    musicPlayer.currentTime = 0;
    musicPlayer = null;
  }
  currentMusicCategory = null;
}

function previewSong(song, duration) {
  if (!song) return;
  if (!_audioUnlocked) return;
  stopMusic();
  duration = duration || 10;
  var audio = new Audio('/music/' + encodeURIComponent(song) + '.mp3');
  audio.volume = 0.3;
  audio.play().catch(function(e) {
    announce('Preview failed: ' + (e.message || 'autoplay blocked'));
  });
  musicPlayer = audio;
  currentMusicCategory = 'preview';
  setTimeout(function() {
    if (currentMusicCategory === 'preview') stopMusic();
  }, duration * 1000);
}

function populateMusicSelects(prefs) {
  var songs = ['', 'ByTheFire', 'Frozen-in-Time', 'Noisescape', 'TranquilReflections', 'Wonder'];
  ['music-main-menu', 'music-forum', 'music-topic'].forEach(function(id) {
    var sel = $(id);
    if (!sel) return;
    sel.innerHTML = '';
    songs.forEach(function(s) {
      var opt = document.createElement('option');
      opt.value = s;
      opt.textContent = s || '(None)';
      sel.appendChild(opt);
    });
    var key = id.replace('music-', '').replace('-', '_');
    if (prefs[key]) sel.value = prefs[key];
  });
}

function saveMusicPrefs() {
  var prefs = {
    main_menu: $('music-main-menu').value,
    forum: $('music-forum').value,
    topic: $('music-topic').value
  };
  sendMsg({ type: 'set_music_prefs', prefs: prefs });
}

function previewSelected(category) {
  var sel = $('music-' + category);
  if (!sel) return;
  var song = sel.value;
  if (song) previewSong(song, 10);
}

function updateMusicForView() {
  if (!username) { stopMusic(); return; }
  if ($('post-section') && !$('post-section').hidden) {
    playMusic('topic');
  } else if ($('topic-section') && !$('topic-section').hidden) {
    playMusic('forum');
  } else {
    playMusic('main_menu');
  }
}

function showView(viewId) {
  document.querySelectorAll('.view').forEach(v => v.hidden = true);
  $(viewId).hidden = false;
  const el = $(viewId);
  if (el) el.focus();
  updateMusicForView();
}

function hideAllSections() {
  $('admin-controls').hidden = true;
  $('top-controls').hidden = true;
  $('forum-section').hidden = true;
  $('topic-section').hidden = true;
  $('post-section').hidden = true;
  $('admin-topic-close-btn').hidden = true;
  $('admin-topic-reopen-btn').hidden = true;
  $('admin-topic-adminonly-btn').hidden = true;
}

function connect() {
  const user = $('login-username').value.trim();
  const pass = $('login-password').value;
  if (!user || !pass) {
    $('login-error').textContent = 'Username and password required';
    $('login-error').hidden = false;
    return;
  }
  doConnect("wss://chatwisp.onrender.com", user, pass, 'login');
}

function doConnect(wsUrl, user, pass, mode) {
  savedWsUrl = wsUrl;
  savedUser = user;
  savedPass = pass;
  savedMode = mode;
  announce('Connecting...');
  ws = new WebSocket(wsUrl);

  ws.onopen = function() {
    announce('Connected. Authenticating...');
    ws.send(JSON.stringify({ type: mode, username: user, password: pass, client_version: "4.0.0" }));
    if (keepaliveInterval) clearInterval(keepaliveInterval);
    keepaliveInterval = setInterval(function() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: "ping", client_time: Date.now() / 1000 })); } catch(e) {}
      }
    }, 30000);
  };

  ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    handleServerMessage(data);
  };

  ws.onclose = function() {
    if (keepaliveInterval) { clearInterval(keepaliveInterval); keepaliveInterval = null; }
    if (authenticated && reconnectAttempts < maxReconnectAttempts) {
      reconnectAttempts++;
      announce('Connection lost, reconnecting (' + reconnectAttempts + '/' + maxReconnectAttempts + ')...');
      setTimeout(function() {
        doConnect(savedWsUrl, savedUser, savedPass, savedMode);
      }, 3000);
      return;
    }
    reconnectAttempts = 0;
    if (keepaliveInterval) { clearInterval(keepaliveInterval); keepaliveInterval = null; }
    if (username) {
      announce('Disconnected from server');
    }
    ws = null;
    authenticated = false;
    username = null;
    isAdmin = false;
    isSuperAdmin = false;
    unreadCount = 0;
    dmContacts = [];
    dmCurrentUser = null;
    showView('view-login');
    hideAllSections();
    $('main-nav').hidden = true;
    $('login-title').textContent = 'Login / Register';
    stopMusic();
  };

  ws.onerror = function() {
    $('login-error').textContent = 'Connection failed. Check server address.';
    $('login-error').hidden = false;
    announce('Connection failed');
  };
}

function handleServerMessage(data) {
  const type = data.type;

  if (type === 'login_success') {
    authenticated = true;
    reconnectAttempts = 0;
    username = data.username;
    isAdmin = data.is_admin;
    isSuperAdmin = data.super_admin || false;
    $('main-nav').hidden = false;
    showMainMenu();
    announce('Welcome, ' + username + '!');
    sendMsg({ type: 'get_music_prefs' });
    if (pendingLink) {
      sendMsg({ type: 'resolve_topic_link', slug: pendingLink.slug });
    }
  } else if (type === 'welcome') {
    alert(data.message);
  } else if (type === 'unread_dms') {
    unreadCount = data.count || 0;
    updateMessagesBadge();
  } else if (type === 'login_error' || type === 'register_error') {
    $('login-error').textContent = data.message;
    $('login-error').hidden = false;
    announce('Authentication failed');
  } else if (type === 'register_success') {
    $('login-error').textContent = 'Registration successful! You can now log in.';
    $('login-error').style.color = '#27ae60';
    $('login-error').hidden = false;
    announce('Registration successful');
  } else if (type === 'forums_list') {
    forumsData = data.forums;
    renderForums(data.forums);
  } else if (type === 'topics_list') {
    topicsData = data.topics;
    renderTopics(data.forum_id, data.topics);
  } else if (type === 'posts_list') {
    currentTopicData = data.topic;
    renderPosts(data.topic, data.posts);
  } else if (type === 'topic_created') {
    announce('Topic created');
    sendMsg({ type: 'get_topics', forum_id: currentForumId });
  } else if (type === 'post_created') {
    announce('Post created');
    if (currentTopicId) {
      sendMsg({ type: 'get_posts', topic_id: currentTopicId });
    }
  } else if (type === 'forum_created') {
    announce('Forum created');
    sendMsg({ type: 'get_forums' });
  } else if (type === 'topic_closed') {
    announce('Topic closed');
    if (currentTopicId === data.topic_id) {
      sendMsg({ type: 'get_posts', topic_id: currentTopicId });
    } else if (currentForumId) {
      sendMsg({ type: 'get_topics', forum_id: currentForumId });
    }
  } else if (type === 'topic_reopened') {
    announce('Topic reopened');
    if (currentTopicId === data.topic_id) {
      sendMsg({ type: 'get_posts', topic_id: currentTopicId });
    } else if (currentForumId) {
      sendMsg({ type: 'get_topics', forum_id: currentForumId });
    }
  } else if (type === 'topic_admin_only_set') {
    announce('Topic set to admin only');
    if (currentTopicId === data.topic_id) {
      sendMsg({ type: 'get_posts', topic_id: currentTopicId });
    } else if (currentForumId) {
      sendMsg({ type: 'get_topics', forum_id: currentForumId });
    }
  } else if (type === 'topic_admin_only_removed') {
    announce('Topic no longer admin only');
    if (currentTopicId === data.topic_id) {
      sendMsg({ type: 'get_posts', topic_id: currentTopicId });
    } else if (currentForumId) {
      sendMsg({ type: 'get_topics', forum_id: currentForumId });
    }
  } else if (type === 'post_deleted') {
    announce('Post deleted');
    if (currentTopicId) {
      sendMsg({ type: 'get_posts', topic_id: currentTopicId });
    }
  } else if (type === 'topic_deleted') {
    announce('Topic deleted');
    showMainMenu();
  } else if (type === 'users_list') {
    renderUsers(data.users);
  } else if (type === 'banned') {
    announce(data.message || 'User banned');
  } else if (type === 'unbanned') {
    announce(data.message || 'User unbanned');
  } else if (type === 'user_deleted') {
    announce(data.message || 'User deleted');
    sendMsg({ type: 'get_users' });
  } else if (type === 'promoted') {
    if (data.username === username) {
      isAdmin = true;
    }
    announce(data.message || 'User promoted');
    alert(data.message || 'User promoted');
  } else if (type === 'demoted') {
    if (data.username === username) {
      isAdmin = false;
    }
    announce(data.message || 'User demoted');
    alert(data.message || 'User demoted');
  } else if (type === 'motd_set') {
    announce(data.message || 'MOTD updated');
    alert(data.message || 'MOTD updated');
  } else if (type === 'password_reset') {
    announce(data.message || 'Password reset');
    alert(data.message || 'Password reset');
  } else if (type === 'search_results') {
    renderDmSearchResults(data.users);
  } else if (type === 'dm_sent') {
    announce('Message sent');
    if (dmCurrentUser) {
      sendMsg({ type: 'get_dm_conversation', username: dmCurrentUser });
    }
  } else if (type === 'dm_received') {
    const dm = data.dm;
    if (dm.sender === username || dm.recipient === username) {
      const other = dm.sender === username ? dm.recipient : dm.sender;
      if (!$('view-dm-chat').hidden && dmCurrentUser === other) {
        sendMsg({ type: 'get_dm_conversation', username: other });
        sendMsg({ type: 'mark_dms_read', username: other });
      } else {
        unreadCount++;
        updateMessagesBadge();
        if ($('view-dm-list').hidden && $('view-dm-search').hidden) {
          alert('New message from ' + other);
        }
      }
    }
  } else if (type === 'dm_conversation') {
    renderDmConversation(data.messages);
  } else if (type === 'dm_contacts') {
    renderDmContacts(data.contacts);
  } else if (type === 'bot_dm_sent') {
    announce('Message sent as official account');
  } else if (type === 'bot_broadcast_complete') {
    announce(data.message);
    alert(data.message);
  } else if (type === 'bot_post_created') {
    announce('Post created as official account');
  } else if (type === 'bot_topic_created') {
    announce('Topic created as official account');
    if (currentForumId) {
      sendMsg({ type: 'get_topics', forum_id: currentForumId });
    }
  } else if (type === 'signature_data') {
    $('sig-input').value = data.signature || '';
    updateSigCounter();
  } else if (type === 'signature_updated') {
    announce('Signature saved');
    alert(data.message || 'Signature updated');
  } else if (type === 'music_prefs_data') {
    musicPrefs = data.prefs || {};
    populateMusicSelects(musicPrefs);
    updateMusicForView();
  } else if (type === 'music_prefs_updated') {
    announce('Music preferences saved');
    alert(data.message || 'Music preferences saved');
  } else if (type === 'topic_link_resolved') {
    const forumId = data.forum_id;
    const topicId = data.topic_id;
    currentForumId = forumId;
    pendingLink = null;
    sendMsg({ type: 'get_topics', forum_id: forumId });
    currentTopicId = topicId;
    pendingLinkTopicId = topicId;
  } else if (type === 'error') {
    announce('Error: ' + data.message);
    alert('Error: ' + data.message);
  }
}

function updateMessagesBadge() {
  const btn = $('messages-btn');
  if (unreadCount > 0) {
    btn.textContent = 'Messages (' + unreadCount + ')';
  } else {
    btn.textContent = 'Messages';
  }
}

function sendMsg(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  } else {
    announce('Not connected');
  }
}

function doLogin() {
  connect();
}

function doRegister() {
  const user = $('login-username').value.trim();
  const pass = $('login-password').value;
  if (!user || !pass) {
    $('login-error').textContent = 'Username and password required';
    $('login-error').hidden = false;
    return;
  }
  if (user.length < 3) {
    $('login-error').textContent = 'Username must be at least 3 characters';
    $('login-error').hidden = false;
    return;
  }
  if (pass.length < 8) {
    $('login-error').textContent = 'Password must be at least 8 characters';
    $('login-error').hidden = false;
    return;
  }
  doConnect("wss://chatwisp.onrender.com", user, pass, 'register');
}

function disconnect() {
  if (ws) {
    ws.close();
  }
}

function showMainMenu() {
  showView('view-main');
  hideAllSections();
  $('forum-section').hidden = false;
  $('admin-controls').hidden = !isAdmin;
  $('section-title').textContent = 'Select Forum';
  $('top-controls').hidden = true;
  currentTopicData = null;
  dmCurrentUser = null;
  announce('Loading forums...');
  sendMsg({ type: 'get_forums' });
  updateMusicForView();
}

// --- Forums ---

function renderForums(forums) {
  const list = $('forum-list');
  list.innerHTML = '';
  forums.forEach(function(f) {
    const div = document.createElement('div');
    div.className = 'forum-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    div.setAttribute('aria-label', 'Forum: ' + f.name + '. Description: ' + f.description);
    div.innerHTML = '<div class="forum-name">' + escapeHtml(f.name) + '</div><div class="forum-desc">' + escapeHtml(f.description) + '</div>';
    div.addEventListener('click', function() { selectForum(f.id); });
    div.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { selectForum(f.id); }
    });
    list.appendChild(div);
  });
  announce(forums.length + ' forums loaded. ' + (_isMobile ? 'Tap a forum to select it.' : 'Use arrow keys to navigate.'));
  if (forums.length > 0) {
    list.firstChild.focus();
  }
}

function selectForum(forumId) {
  currentForumId = forumId;
  hideAllSections();
  $('top-controls').hidden = false;
  $('topic-section').hidden = false;
  $('forum-section').hidden = false;
  $('section-title').textContent = 'Topics';
  announce('Loading topics...');
  sendMsg({ type: 'get_topics', forum_id: forumId });
  updateMusicForView();
}

// --- Topics ---

function renderTopics(forumId, topics) {
  const list = $('topic-list');
  list.innerHTML = '';
  topics.forEach(function(t) {
    const div = document.createElement('div');
    div.className = 'topic-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    const statusStr = t.closed ? ' [CLOSED]' : '';
    const adminStr = t.admin_only ? ' [ADMIN ONLY]' : '';
    div.setAttribute('aria-label', 'Topic: ' + t.title + '. By ' + t.author + statusStr + adminStr + '. ' + t.post_count + ' posts.');
    div.innerHTML = '<div class="topic-title">' + escapeHtml(t.title) + statusStr + adminStr + '</div><div class="topic-meta">By ' + escapeHtml(t.author) + ' - ' + t.post_count + ' posts</div>';
    div.addEventListener('click', function() { selectTopic(t.id); });
    div.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { selectTopic(t.id); }
    });
    list.appendChild(div);
    if (isAdmin) {
      try {
        var actions = [];
        if (!t.closed) {
          actions.push({ name: 'Close Topic', action: function() {
            sendMsg({ type: 'close_topic', topic_id: t.id });
            announce('Closing topic...');
          }});
        }
        actions.push({ name: 'Delete Topic', action: function() {
          if (confirm('Delete this entire topic and all its posts? This cannot be undone.')) {
            sendMsg({ type: 'delete_topic', topic_id: t.id });
            announce('Deleting topic...');
          }
        }});
        div.accessibilityActions = actions;
      } catch(e) {}
      var actionLabels = 'Delete Topic';
      if (!t.closed) actionLabels += ', Close Topic';
      div.setAttribute('aria-actions', actionLabels);
    }
  });
  announce(topics.length + ' topics loaded.');
  if (pendingLinkTopicId) {
    const tid = pendingLinkTopicId;
    pendingLinkTopicId = null;
    selectTopic(tid);
    return;
  }
  if (topics.length > 0) {
    list.firstChild.focus();
  }
}

function selectTopic(topicId) {
  currentTopicId = topicId;
  hideAllSections();
  $('forum-section').hidden = false;
  $('topic-section').hidden = false;
  $('post-section').hidden = false;
  announce('Loading posts...');
  sendMsg({ type: 'get_posts', topic_id: topicId });
  updateMusicForView();
}

// --- Posts ---

function renderPosts(topic, posts) {
  const list = $('post-list');
  list.innerHTML = '';
  currentTopicSlug = topic.slug || null;
  const statusTags = (topic.closed ? ' [CLOSED]' : '') + (topic.admin_only ? ' [ADMIN ONLY]' : '');
  $('post-section-title').textContent = 'Topic: ' + topic.title + statusTags;

  posts.forEach(function(p) {
    const div = document.createElement('div');
    div.className = 'post-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    div.setAttribute('aria-label', p.author + ' said: ' + p.content + (p.signature ? ' — ' + p.signature : ''));

    const authorDiv = document.createElement('div');
    authorDiv.className = 'post-author';
    authorDiv.textContent = p.author + ' said:';
    div.appendChild(authorDiv);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'post-content';
    let contentText = p.content;
    if (p.signature) {
      contentText += '\n— ' + p.signature;
    }
    contentDiv.textContent = contentText;
    div.appendChild(contentDiv);

    if (isAdmin) {
      const delBtn = document.createElement('button');
      delBtn.className = 'post-delete-btn';
      delBtn.textContent = 'Delete';
      delBtn.setAttribute('aria-label', 'Delete post by ' + p.author);
      (function(postId) {
        delBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          if (confirm('Delete this post? This cannot be undone.')) {
            sendMsg({ type: 'delete_post', post_id: postId });
            announce('Deleting post...');
          }
        });
      })(p.id);
      div.appendChild(delBtn);
    }

    list.appendChild(div);

    if (isAdmin) {
      try {
        div.accessibilityActions = [
          { name: 'Delete Post', action: function() {
            if (confirm('Delete this post? This cannot be undone.')) {
              sendMsg({ type: 'delete_post', post_id: p.id });
              announce('Deleting post...');
            }
          }}
        ];
      } catch(e) {}
      div.setAttribute('aria-actions', 'Delete Post');
    }
  });

  $('reply-area').hidden = topic.closed || (topic.admin_only && !isAdmin);
  $('topic-closed-msg').hidden = !topic.closed;
  $('topic-adminonly-msg').hidden = !topic.admin_only;

  if (isAdmin) {
    $('admin-topic-close-btn').hidden = topic.closed;
    $('admin-topic-reopen-btn').hidden = !topic.closed;
    $('admin-topic-adminonly-btn').hidden = false;
    $('admin-topic-adminonly-btn').textContent = topic.admin_only ? 'Remove Admin Only' : 'Make Admin Only';
    $('admin-topic-delete-btn').hidden = false;
    $('top-controls').hidden = false;
  } else {
    $('admin-topic-close-btn').hidden = true;
    $('admin-topic-reopen-btn').hidden = true;
  $('admin-topic-adminonly-btn').hidden = true;
  $('admin-topic-delete-btn').hidden = true;
    $('admin-topic-delete-btn').hidden = true;
  }

  announce(posts.length + ' posts loaded.');
  if (posts.length > 0) {
    list.firstChild.focus();
  }
}

// --- Admin: Delete Topic, Admin Only Toggle ---

function adminDeleteTopic() {
  if (!isAdmin || !currentTopicId) return;
  if (confirm('Delete this entire topic and all its posts? This cannot be undone.')) {
    sendMsg({ type: 'delete_topic', topic_id: currentTopicId });
    announce('Deleting topic...');
  }
}

function adminToggleAdminOnly() {
  if (!isAdmin || !currentTopicId || !currentTopicData) return;
  if (currentTopicData.admin_only) {
    sendMsg({ type: 'remove_topic_admin_only', topic_id: currentTopicId });
    announce('Removing admin only...');
  } else {
    sendMsg({ type: 'set_topic_admin_only', topic_id: currentTopicId });
    announce('Setting admin only...');
  }
}

function showSetMotd() {
  const motd = prompt('Enter the new Message of the Day:');
  if (motd && motd.trim()) {
    sendMsg({ type: 'set_motd', motd: motd.trim() });
    announce('Setting MOTD...');
  }
}

function showCreateTopic() {
  showView('view-create-topic');
  $('topic-title').value = '';
  $('topic-content').value = '';
  $('topic-admin-only').checked = false;
  $('topic-admin-only-group').hidden = !isAdmin;
  $('topic-title').focus();
  announce('Create new topic form');
}

function submitCreateTopic() {
  const title = $('topic-title').value.trim();
  const content = $('topic-content').value.trim();
  if (!title) {
    alert('Topic title is required');
    $('topic-title').focus();
    return;
  }
  const fid = currentForumId;
  const admin_only = isAdmin && $('topic-admin-only').checked;
  sendMsg({ type: 'create_topic', forum_id: fid, title: title, content: content, admin_only: admin_only });
  showView('view-main');
  hideAllSections();
  $('forum-section').hidden = false;
  $('topic-section').hidden = false;
  $('top-controls').hidden = false;
  $('section-title').textContent = 'Topics';
  $('topic-list').innerHTML = '<p>Creating topic...</p>';
  announce('Creating topic...');
}

function showCreateForum() {
  showView('view-create-forum');
  $('forum-name').value = '';
  $('forum-desc').value = '';
  $('forum-name').focus();
  announce('Create new forum form');
}

function submitCreateForum() {
  const name = $('forum-name').value.trim();
  const desc = $('forum-desc').value.trim();
  if (!name) {
    alert('Forum name is required');
    $('forum-name').focus();
    return;
  }
  sendMsg({ type: 'create_forum', name: name, description: desc });
  showView('view-main');
  hideAllSections();
  $('forum-section').hidden = false;
  $('section-title').textContent = 'Select Forum';
  $('forum-list').innerHTML = '<p>Creating forum...</p>';
  announce('Creating forum...');
}

function sendReply() {
  const content = $('reply-content').value.trim();
  if (!content) {
    alert('Post content is required');
    $('reply-content').focus();
    return;
  }
  sendMsg({ type: 'create_post', topic_id: currentTopicId, content: content });
  $('reply-content').value = '';
  announce('Sending reply...');
}

function adminCloseTopic() {
  if (!isAdmin) { announce('Admin access required'); return; }
  if (!currentTopicId) return;
  sendMsg({ type: 'close_topic', topic_id: currentTopicId });
  announce('Closing topic...');
}

function adminReopenTopic() {
  if (!isAdmin) { announce('Admin access required'); return; }
  if (!currentTopicId) return;
  sendMsg({ type: 'reopen_topic', topic_id: currentTopicId });
  announce('Reopening topic...');
}

function copyTopicLink() {
  if (!currentForumId || !currentTopicSlug) {
    announce('No topic link available');
    return;
  }
  const url = 'https://chatwisp.onrender.com/forums/' + currentForumId + '/' + currentTopicSlug;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(function() {
      announce('Topic link copied to clipboard');
    }).catch(function() {
      prompt('Copy this link:', url);
    });
  } else {
    prompt('Copy this link:', url);
  }
}

// --- Accounts ---

function showAccounts() {
  announce('Loading users...');
  sendMsg({ type: 'get_users' });
}

function renderUsers(users) {
  showView('view-accounts');
  updateMusicForView();
  const list = $('users-list');
  list.innerHTML = '';
  users.forEach(function(u) {
    const div = document.createElement('div');
    div.className = 'user-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    let label = 'Username: ' + u.username;
    if (u.is_admin) label += ', Admin';
    if (u.super_admin) label += ', Super Admin';
    if (u.banned) label += ', Banned' + (u.ban_reason ? ': ' + u.ban_reason : '');
    if (u.ban_remaining) label += ', Remaining time: ' + u.ban_remaining;
    div.setAttribute('aria-label', label);
    let html = '<div class="user-name">' + escapeHtml(u.username) + '</div><div class="user-meta">';
    if (u.super_admin) html += '[Super Admin] ';
    else if (u.is_admin) html += '[Admin] ';
    if (u.banned) html += '[Banned' + (u.ban_reason ? ': ' + escapeHtml(u.ban_reason) : '') + ']';
    if (u.ban_remaining) html += ' [Remaining: ' + escapeHtml(u.ban_remaining) + ']';
    html += '</div>';
    div.innerHTML = html;
    div.addEventListener('click', function() { userAction(u); });
    div.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { userAction(u); }
    });
    list.appendChild(div);
  });
  announce(users.length + ' users loaded.');
  if (users.length > 0) {
    list.firstChild.focus();
  }
}

function userAction(user) {
  let options = 'Actions for ' + user.username + ':\n\n' +
    '1 - Ban user\n' +
    (user.banned ? '2 - Unban user\n' : '') +
    (user.username !== username ? '3 - Delete user\n' : '');
  if (!user.super_admin && user.username !== username) {
    if (!user.is_admin) {
      options += '4 - Promote to Admin\n';
    } else if (!user.super_admin) {
      options += '5 - Demote from Admin\n';
    }
  }
  if (isAdmin && user.username !== username) {
    options += '6 - Reset Password\n';
  }
  options += '\nEnter number:';
  const action = prompt(options);
  if (!action) return;
  if (action === '1') {
    const reason = prompt('Ban reason (optional, leave blank for none):');
    const duration = prompt('Duration (optional, leave blank for infinite, e.g. 1h30m, 2d):');
    sendMsg({ type: 'ban_user', username: user.username, reason: reason || null, duration: duration || null });
    announce('Banning ' + user.username + '...');
  } else if (action === '2' && user.banned) {
    sendMsg({ type: 'unban_user', username: user.username });
    announce('Unbanning ' + user.username + '...');
  } else if (action === '3') {
    if (confirm('Are you sure you want to delete user ' + user.username + '? This cannot be undone.')) {
      sendMsg({ type: 'delete_user', username: user.username });
      announce('Deleting ' + user.username + '...');
    }
  } else if (action === '4') {
    if (confirm('Promote ' + user.username + ' to admin?')) {
      sendMsg({ type: 'promote_admin', username: user.username });
      announce('Promoting ' + user.username + '...');
    }
  } else if (action === '5') {
    if (confirm('Demote ' + user.username + ' from admin?')) {
      sendMsg({ type: 'demote_admin', username: user.username });
      announce('Demoting ' + user.username + '...');
    }
  } else if (action === '6') {
    const newPass = prompt('Enter new password for ' + user.username + ':');
    if (newPass && newPass.length >= 8) {
      const confirmPass = prompt('Confirm new password:');
      if (confirmPass === newPass) {
        if (confirm('Reset password for ' + user.username + '?')) {
          sendMsg({ type: 'reset_password', username: user.username, new_password: newPass });
          announce('Resetting password...');
        }
      } else {
        alert('Passwords do not match');
      }
    } else if (newPass) {
      alert('Password must be at least 8 characters');
    }
  }
}

// --- Private Messages ---

function showDmList() {
  showView('view-dm-list');
  announce('Loading conversations...');
  sendMsg({ type: 'get_dm_contacts' });
}

function renderDmContacts(contacts) {
  dmContacts = contacts;
  const list = $('dm-contacts-list');
  list.innerHTML = '';
  if (contacts.length === 0) {
    list.innerHTML = '<p>No conversations yet. Start a new message.</p>';
    return;
  }
  contacts.forEach(function(c) {
    const div = document.createElement('div');
    div.className = 'forum-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    div.setAttribute('aria-label', 'Chat with ' + c.username + '. Last message: ' + c.last_message);
    div.innerHTML = '<div class="forum-name">' + escapeHtml(c.username) + '</div><div class="forum-desc">' + escapeHtml(c.last_message) + '</div>';
    div.addEventListener('click', function() { selectDmContact(c.username); });
    div.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { selectDmContact(c.username); }
    });
    list.appendChild(div);
  });
  announce(contacts.length + ' conversations. ' + (_isMobile ? 'Tap a conversation to open it.' : 'Use arrow keys to navigate.'));
  if (contacts.length > 0) {
    list.firstChild.focus();
  }
}

function showDmSearch() {
  showView('view-dm-search');
  $('dm-search-input').value = '';
  $('dm-search-results').innerHTML = '';
  $('dm-search-input').focus();
  announce('Search for a user to message');
}

function searchUsers() {
  const query = $('dm-search-input').value.trim();
  if (query.length < 1) {
    $('dm-search-results').innerHTML = '';
    return;
  }
  sendMsg({ type: 'search_users', query: query });
}

function renderDmSearchResults(users) {
  const list = $('dm-search-results');
  list.innerHTML = '';
  if (users.length === 0) {
    list.innerHTML = '<p>No users found.</p>';
    return;
  }
  users.forEach(function(u) {
    const div = document.createElement('div');
    div.className = 'forum-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    div.setAttribute('aria-label', 'User: ' + u);
    div.innerHTML = '<div class="forum-name">' + escapeHtml(u) + '</div>';
    div.addEventListener('click', function() { selectDmContact(u); });
    div.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { selectDmContact(u); }
    });
    list.appendChild(div);
  });
  announce(users.length + ' users found.');
  if (users.length > 0) {
    list.firstChild.focus();
  }
}

function selectDmContact(other) {
  dmCurrentUser = other;
  showView('view-dm-chat');
  $('dm-chat-title').textContent = 'Chat with ' + other;
  $('dm-message-list').innerHTML = '<p>Loading messages...</p>';
  if (other === 'Chatwisp Official Account') {
    $('dm-reply-area').hidden = true;
    $('dm-bot-noreply').hidden = false;
  } else {
    $('dm-reply-area').hidden = false;
    $('dm-bot-noreply').hidden = true;
    $('dm-input').value = '';
    $('dm-input').focus();
  }
  sendMsg({ type: 'get_dm_conversation', username: other });
  sendMsg({ type: 'mark_dms_read', username: other });
}

function renderDmConversation(messages) {
  const list = $('dm-message-list');
  list.innerHTML = '';
  if (messages.length === 0) {
    list.innerHTML = '<p>No messages yet. Send a message to start the conversation.</p>';
    $('dm-input').focus();
    return;
  }
  messages.forEach(function(m) {
    const div = document.createElement('div');
    div.className = 'post-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    const isMine = m.sender === username;
    const label = isMine ? 'You' : m.sender;
    div.setAttribute('aria-label', label + ' said: ' + m.content);
    div.innerHTML = '<div class="post-author">' + (isMine ? 'You' : escapeHtml(m.sender)) + ' said:</div><div class="post-content">' + escapeHtml(m.content) + '</div>';
    list.appendChild(div);
  });
  announce(messages.length + ' messages.');
  if (dmCurrentUser === 'Chatwisp Official Account') {
    if (list.lastChild) list.lastChild.focus();
  } else {
    list.lastChild ? list.lastChild.focus() : $('dm-input').focus();
  }
}

function sendDm() {
  const content = $('dm-input').value.trim();
  if (!content || !dmCurrentUser) return;
  sendMsg({ type: 'send_dm', recipient: dmCurrentUser, content: content });
  $('dm-input').value = '';
  announce('Sending message...');
}

// --- Utilities ---

function showBotControls() {
  showView('view-bot-controls');
  $('bot-dm-recipient').value = '';
  $('bot-dm-content').value = '';
  $('bot-broadcast-content').value = '';
  $('bot-post-topic').value = '';
  $('bot-post-content').value = '';
  $('bot-topic-forum').value = '';
  $('bot-topic-title').value = '';
  $('bot-topic-content').value = '';
  $('bot-dm-recipient').focus();
  announce('Official account controls');
}

function botSendDm() {
  const recipient = $('bot-dm-recipient').value.trim();
  const content = $('bot-dm-content').value.trim();
  if (!recipient || !content) {
    alert('Recipient and content required');
    return;
  }
  sendMsg({ type: 'bot_send_dm', recipient: recipient, content: content });
  announce('Sending DM as official account...');
}

function botBroadcast() {
  const content = $('bot-broadcast-content').value.trim();
  if (!content) {
    alert('Content required');
    return;
  }
  if (confirm('Broadcast this message to ALL users? This cannot be undone.')) {
    sendMsg({ type: 'bot_broadcast', content: content });
    announce('Broadcasting...');
  }
}

function botCreatePost() {
  const topic_id = $('bot-post-topic').value.trim();
  const content = $('bot-post-content').value.trim();
  if (!topic_id || !content) {
    alert('Topic ID and content required');
    return;
  }
  sendMsg({ type: 'bot_create_post', topic_id: topic_id, content: content });
  announce('Creating post as official account...');
}

function botCreateTopic() {
  const forum_id = $('bot-topic-forum').value.trim();
  const title = $('bot-topic-title').value.trim();
  const content = $('bot-topic-content').value.trim();
  if (!forum_id || !title) {
    alert('Forum ID and title required');
    return;
  }
  sendMsg({ type: 'bot_create_topic', forum_id: forum_id, title: title, content: content });
  announce('Creating topic as official account...');
}

function showSettings() {
  showView('view-settings');
  sendMsg({ type: 'get_signature' });
  $('sig-input').focus();
  updateSigCounter();
  announce('Settings');
  updateMusicForView();
  sendMsg({ type: 'get_music_prefs' });
}

function updateSigCounter() {
  const len = $('sig-input').value.length;
  $('sig-counter').textContent = len + '/50';
}

function saveSignature() {
  const sig = $('sig-input').value.trim();
  if (sig.length > 50) {
    alert('Signature must be 50 characters or less');
    return;
  }
  sendMsg({ type: 'set_signature', signature: sig });
  announce('Saving signature...');
}

function continueInBrowser() {
  $('topic-link-choice').hidden = true;
  $('login-username').focus();
  announce('Log in to go directly to the topic');
}


function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', function() {
  const match = window.location.pathname.match(/^\/forums\/([^/]+)\/([^/]+)$/);
  if (match) {
    pendingLink = { forum_id: match[1], slug: match[2] };
    $('topic-link-choice').hidden = false;
    $('choice-browser-btn').focus();
    announce('Topic link detected. Choose where to open it.');
  }
  $('login-username').focus();
});

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    if (!$('view-login').hidden) {
      return;
    }
    if (!$('view-dm-chat').hidden) {
      showDmList();
    } else if (!$('view-dm-search').hidden) {
      showDmList();
    } else if (!$('view-dm-list').hidden) {
      showMainMenu();
    } else if (!$('view-accounts').hidden) {
      showMainMenu();
    } else if (!$('view-create-topic').hidden) {
      showMainMenu();
    } else if (!$('view-create-forum').hidden) {
      showMainMenu();
    } else if (!$('post-section').hidden) {
      showMainMenu();
    } else if (!$('topic-section').hidden && $('forum-section').hidden === false) {
      showMainMenu();
    } else {
      showMainMenu();
    }
    e.preventDefault();
  }
});
