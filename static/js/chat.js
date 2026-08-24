// 3차 static/app.js 의 chat 부분 이식본.
//
// 바뀐 것:
//   - fetch 경로: /api/chat 계열 → chat-config JSON 의 Django URL
//   - POST 에 X-CSRFToken 헤더 (Django CSRF. credentials 는 same-origin 기본)
//   - session id: Date.now() 문자열 → 서버 pk. "새 대화"는 serverId=null 인
//     로컬 세션으로 시작하고, 첫 질문의 응답이 준 pk 를 붙인다
//   - 대화 삭제가 서버에도 반영 (3차는 화면에서만 지워져 새로고침 시 부활)
//   - escapeHtml(): 3차는 사용자 입력·답변을 innerHTML 에 그대로 넣어
//     XSS 가 가능했다. 렌더링 직전에 이스케이프한다
// 유지한 것: 세션 복원·전환, 말풍선/팁/출처 렌더링, 타이핑 표시,
//   인기 질문으로 빠른 질문 갱신, 사이드바 리사이즈 — 3차 로직 그대로.

const CONFIG = JSON.parse(document.getElementById('chat-config').textContent);

let chatSessions = [];
let currentLocalId = null;   // 화면 전환용 로컬 id (서버 pk 와 별개)
let isTyping = false;
let localSeq = 0;

const DEFAULT_QUICK_QUESTIONS = [
  '배달 용기 분리수거 어떻게 해?',
  '페트병 라벨 꼭 떼야 해?',
  '음식물쓰레기 배출 방법 알려줘',
  '뼈다귀는 음식물쓰레기야?',
];
let quickQuestions = [...DEFAULT_QUICK_QUESTIONS];

// ===== 공통 유틸 =====
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}

function postJson(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: JSON.stringify(body),
  });
}

function currentSession() {
  return chatSessions.find(s => s.localId === currentLocalId);
}

// ===== 세션 복원 (3차 restoreAllSessions) =====
async function restoreAllSessions() {
  try {
    const res = await fetch(CONFIG.urls.sessions);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const groups = await res.json();

    if (!groups.length) { createNewSession(); return; }

    chatSessions = groups.map(g => {
      const firstUserMsg = g.messages.find(m => m.role === 'user');
      const title = firstUserMsg
        ? (firstUserMsg.content.length > 20 ? firstUserMsg.content.substring(0, 20) + '...' : firstUserMsg.content)
        : '대화';
      return {
        localId: 'srv-' + g.session_id,
        serverId: g.session_id,
        title,
        region: g.region,
        messages: g.messages.map(m => ({
          role: m.role === 'user' ? 'user' : 'bot',
          content: m.content,
          tip: m.tip || '',
          source: m.source || '',
          sources: m.sources || [],
        })),
      };
    });
    currentLocalId = chatSessions[0].localId;
    document.getElementById('region-select').value = chatSessions[0].region;

    renderChatList(); renderMessages(); updateRegionBadge(); updateChatTitle();
  } catch (err) {
    console.error('대화 기록 복원 실패:', err);
    createNewSession();
  }
}

// ===== 세션 관리 (3차 createNewSession / switchSession / deleteSession) =====
function createNewSession() {
  const region = document.getElementById('region-select').value;
  const session = {
    localId: 'new-' + (++localSeq),
    serverId: null,          // 첫 질문에서 서버가 pk 를 발급
    title: '새 대화',
    region,
    messages: [],
  };
  chatSessions.unshift(session);
  currentLocalId = session.localId;
  renderChatList(); renderMessages(); updateRegionBadge(); updateChatTitle();
}

function switchSession(localId) {
  currentLocalId = localId;
  const session = currentSession();
  if (session) document.getElementById('region-select').value = session.region;
  renderChatList(); renderMessages(); updateRegionBadge(); updateChatTitle();
}

async function deleteSession(localId, e) {
  e.stopPropagation();
  const session = chatSessions.find(s => s.localId === localId);
  if (session && session.serverId) {
    try {
      await postJson(CONFIG.urls.sessionDelete.replace('/0/', '/' + session.serverId + '/'), {});
    } catch (err) { console.error('대화 삭제 실패:', err); }
  }
  chatSessions = chatSessions.filter(s => s.localId !== localId);
  if (!chatSessions.length) { createNewSession(); return; }
  if (currentLocalId === localId) {
    currentLocalId = chatSessions[0].localId;
    document.getElementById('region-select').value = chatSessions[0].region;
  }
  renderChatList(); renderMessages(); updateRegionBadge(); updateChatTitle();
}

function renderChatList() {
  const list = document.getElementById('chat-list');
  list.innerHTML = chatSessions.map(s => `
    <div class="chat-item ${s.localId === currentLocalId ? 'active' : ''}"
         onclick="switchSession('${s.localId}')">
      <span class="chat-item-icon">💬</span>
      <span>${escapeHtml(s.title)}</span>
      <button class="chat-item-delete" onclick="deleteSession('${s.localId}', event)" title="삭제">✕</button>
    </div>
  `).join('');
}

// ===== 지역 =====
document.getElementById('region-select').addEventListener('change', function () {
  updateRegionBadge();
  const session = currentSession();
  if (session) session.region = this.value;
});

function updateRegionBadge() {
  const sel = document.getElementById('region-select');
  document.getElementById('region-badge').textContent = sel.options[sel.selectedIndex].text;
}

function updateChatTitle() {
  const session = currentSession();
  document.getElementById('chat-title').textContent = session ? session.title : '새 대화';
}

// ===== 메시지 렌더링 (3차 renderMessages — 말풍선 구조 그대로) =====
function renderMessages() {
  const container = document.getElementById('chat-messages');
  const session = currentSession();

  if (!session || session.messages.length === 0) {
    container.innerHTML = `
      <div class="welcome-message" id="welcome-message">
        <div class="welcome-icon">🌿</div>
        <h3>Ecobot에 오신 것을 환영합니다</h3>
        <p>환경 실천에 관한 질문을 해보세요!</p>
        <div class="quick-questions">
          ${quickQuestions.map(q => `<button class="quick-q" onclick="sendQuickQuestion(this.textContent)">${escapeHtml(q)}</button>`).join('')}
        </div>
      </div>`;
    return;
  }

  container.innerHTML = session.messages.map(msg => {
    if (msg.role === 'user') {
      return `
        <div class="message user">
          <div class="message-avatar">${escapeHtml(CONFIG.userName[0] || 'U').toUpperCase()}</div>
          <div class="message-content">${escapeHtml(msg.content)}</div>
        </div>`;
    }
    const tipHtml = msg.tip
      ? `<div class="response-tip"><div class="tip-label"><span class="tip-icon">💡</span> 실천 팁</div><p>${escapeHtml(msg.tip)}</p></div>`
      : '';
    const sourceLabel = msg.source || (msg.sources && msg.sources.length
      ? msg.sources.map(s => s.title).join(', ')
      : '');
    const sourcesHtml = sourceLabel ? `<div class="response-source">출처: ${escapeHtml(sourceLabel)}</div>` : '';
    return `
      <div class="message bot">
        <div class="message-avatar">🌿</div>
        <div class="message-content">
          <div class="response-answer">
            <div class="answer-label"><span class="answer-icon">📋</span> 답변</div>
            <p>${escapeHtml(msg.content)}</p>
          </div>
          ${tipHtml}
          ${sourcesHtml}
        </div>
      </div>`;
  }).join('');

  container.scrollTop = container.scrollHeight;
}

// ===== 전송 (3차 sendMessage / addUserMessage / askBackend) =====
function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text || isTyping) return;
  addUserMessage(text);
  input.value = '';
  askBackend(text);
}

function sendQuickQuestion(text) {
  if (isTyping) return;
  addUserMessage(text);
  askBackend(text);
}

function addUserMessage(text) {
  const session = currentSession();
  if (!session) return;
  session.messages.push({ role: 'user', content: text });
  if (session.messages.length === 1) {
    session.title = text.length > 20 ? text.substring(0, 20) + '...' : text;
    renderChatList(); updateChatTitle();
  }
  renderMessages();
}

async function askBackend(question) {
  isTyping = true;
  const container = document.getElementById('chat-messages');

  const typingDiv = document.createElement('div');
  typingDiv.className = 'message bot';
  typingDiv.id = 'typing-indicator';
  typingDiv.innerHTML = `
    <div class="message-avatar">🌿</div>
    <div class="message-content">
      <div class="typing-indicator">
        <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
      </div>
    </div>`;
  container.appendChild(typingDiv);
  container.scrollTop = container.scrollHeight;

  const session = currentSession();
  try {
    const res = await postJson(CONFIG.urls.ask, {
      question,
      region: session ? session.region : document.getElementById('region-select').value,
      session_id: session && session.serverId ? session.serverId : null,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();  // {session_id, answer, tip, source, sources}

    if (session) {
      session.serverId = data.session_id;          // 새 대화면 서버 pk 를 붙인다
      session.messages.push({
        role: 'bot',
        content: data.answer,
        tip: data.tip || '',
        source: data.source || '',
        sources: data.sources || [],
      });
    }
  } catch (err) {
    console.error('챗봇 응답 실패:', err);
    if (session) {
      session.messages.push({
        role: 'bot',
        content: '답변을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.',
        sources: [],
      });
    }
  }

  const typing = document.getElementById('typing-indicator');
  if (typing) typing.remove();
  isTyping = false;
  renderMessages();
  loadPopularQuestions(true);   // 질문 후 인기 질문 갱신 (3차 동일)
}

// ===== 인기 질문 → 빠른 질문 (3차 동일) =====
let _popularCache = null;
async function fetchPopularQuestions(forceRefresh = false) {
  if (_popularCache && !forceRefresh) return _popularCache;
  try {
    const res = await fetch(CONFIG.urls.popular + '?limit=5');
    if (!res.ok) return [];
    _popularCache = await res.json();
    return _popularCache;
  } catch (_) { return []; }
}
async function loadPopularQuestions(forceRefresh = false) {
  const data = await fetchPopularQuestions(forceRefresh);
  if (data.length >= 4) {
    quickQuestions = data.slice(0, 4).map(d => d.question);
    const session = currentSession();
    if (!session || !session.messages.length) renderMessages();
  }
}

// ===== 입력 이벤트 =====
document.getElementById('chat-input').addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
document.getElementById('send-btn').addEventListener('click', sendMessage);
document.getElementById('new-chat-btn').addEventListener('click', createNewSession);

// ===== 사이드바 리사이즈 (3차 initSidebarResize 그대로) =====
(function initSidebarResize() {
  const handle = document.getElementById('sidebar-resize-handle');
  const sidebar = document.getElementById('sidebar');
  if (!handle || !sidebar) return;
  let dragging = false;
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault(); dragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    sidebar.style.width = Math.min(500, Math.max(200, e.clientX)) + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

// ===== 초기화 =====
updateRegionBadge();
restoreAllSessions();
loadPopularQuestions();
