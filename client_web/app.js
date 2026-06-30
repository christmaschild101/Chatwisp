let ws = null;
let username = null;
let isAdmin = false;
let isSuperAdmin = false;
let currentForumId = null;
let currentTopicId = null;
let forumsData = [];
let topicsData = [];
let currentTopicData = null;

function $(id) { return document.getElementById(id); }

function announce(msg) {
  const status = $('status-bar');
  status.textContent = msg;
}

function showView(viewId) {
  document.querySelectorAll('.view').forEach(v => v.hidden = true);
  $(viewId).hidden = false;
  $(viewId).focus();
}

function hideAllSections() {
  $('admin-controls').hidden = true;
  $('top-controls').hidden = true;
  $('forum-section').hidden = true;
  $('topic-section').hidden = true;
  $('post-section').hidden = true;
  $('admin-topic-close-btn').hidden = true;
  $('admin-topic-reopen-btn').hidden = true;
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
  announce('Connecting...');
  ws = new WebSocket(wsUrl);

  ws.onopen = function() {
    announce('Connected. Authenticating...');
    ws.send(JSON.stringify({ type: mode, username: user, password: pass }));
  };

  ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    handleServerMessage(data);
  };

  ws.onclose = function() {
    if (username) {
      announce('Disconnected from server');
    }
    ws = null;
    username = null;
    isAdmin = false;
    isSuperAdmin = false;
    showView('view-login');
    hideAllSections();
    $('main-nav').hidden = true;
    $('login-title').textContent = 'Login / Register';
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
    username = data.username;
    isAdmin = data.is_admin;
    isSuperAdmin = data.super_admin || false;
    $('main-nav').hidden = false;
    showMainMenu();
    announce('Welcome, ' + username + '!');
  } else if (type === 'welcome') {
    alert(data.message);
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
  } else if (type === 'error') {
    announce('Error: ' + data.message);
    alert('Error: ' + data.message);
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
  if (pass.length < 4) {
    $('login-error').textContent = 'Password must be at least 4 characters';
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
  announce('Loading forums...');
  sendMsg({ type: 'get_forums' });
}

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
  announce(forums.length + ' forums loaded. Use arrow keys to navigate.');
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
}

function renderTopics(forumId, topics) {
  const list = $('topic-list');
  list.innerHTML = '';
  topics.forEach(function(t) {
    const div = document.createElement('div');
    div.className = 'topic-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    const statusStr = t.closed ? ' [CLOSED]' : '';
    div.setAttribute('aria-label', 'Topic: ' + t.title + '. By ' + t.author + statusStr + '. ' + t.post_count + ' posts.');
    div.innerHTML = '<div class="topic-title">' + escapeHtml(t.title) + statusStr + '</div><div class="topic-meta">By ' + escapeHtml(t.author) + ' - ' + t.post_count + ' posts</div>';
    div.addEventListener('click', function() { selectTopic(t.id); });
    div.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { selectTopic(t.id); }
    });
    list.appendChild(div);
  });
  announce(topics.length + ' topics loaded.');
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
}

function renderPosts(topic, posts) {
  const list = $('post-list');
  list.innerHTML = '';
  $('post-section-title').textContent = 'Topic: ' + topic.title + (topic.closed ? ' [CLOSED]' : '');

  posts.forEach(function(p) {
    const div = document.createElement('div');
    div.className = 'post-item';
    div.setAttribute('role', 'listitem');
    div.setAttribute('tabindex', '0');
    div.setAttribute('aria-label', p.author + ' said: ' + p.content);
    div.innerHTML = '<div class="post-author">' + escapeHtml(p.author) + ' said:</div><div class="post-content">' + escapeHtml(p.content) + '</div>';
    list.appendChild(div);
  });

  $('reply-area').hidden = topic.closed;
  $('topic-closed-msg').hidden = !topic.closed;

  if (isAdmin) {
    $('admin-topic-close-btn').hidden = topic.closed;
    $('admin-topic-reopen-btn').hidden = !topic.closed;
    $('top-controls').hidden = false;
  } else {
    $('admin-topic-close-btn').hidden = true;
    $('admin-topic-reopen-btn').hidden = true;
  }

  announce(posts.length + ' posts loaded.');
  if (posts.length > 0) {
    list.firstChild.focus();
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
  sendMsg({ type: 'create_topic', forum_id: fid, title: title, content: content });
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

function showAccounts() {
  announce('Loading users...');
  sendMsg({ type: 'get_users' });
}

function renderUsers(users) {
  showView('view-accounts');
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
    div.setAttribute('aria-label', label);
    let html = '<div class="user-name">' + escapeHtml(u.username) + '</div><div class="user-meta">';
    if (u.super_admin) html += '[Super Admin] ';
    else if (u.is_admin) html += '[Admin] ';
    if (u.banned) html += '[Banned' + (u.ban_reason ? ': ' + escapeHtml(u.ban_reason) : '') + ']';
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
    '3 - Delete user\n';
  if (!user.super_admin && user.username !== username) {
    if (!user.is_admin) {
      options += '4 - Promote to Admin\n';
    } else if (!user.super_admin) {
      options += '5 - Demote from Admin\n';
    }
  }
  options += '\nEnter number:';
  const action = prompt(options);
  if (!action) return;
  if (action === '1') {
    const reason = prompt('Ban reason (optional, leave blank for none):');
    const duration = prompt('Duration (optional, leave blank for infinite):');
    if (duration) {
      alert('Ban duration feature is not implemented yet');
    }
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
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', function() {
  $('login-username').focus();
});

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    if (!$('view-login').hidden) {
      return;
    }
    if (!$('view-accounts').hidden) {
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
