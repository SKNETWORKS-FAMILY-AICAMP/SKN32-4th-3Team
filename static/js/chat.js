// 4차 챗봇 JS — 3차 app.js 이식 + UI 리디자인.

const CONFIG = JSON.parse(document.getElementById('chat-config').textContent);

let chatSessions = [];
let currentLocalId = null;
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

// ===== 세션 복원 =====
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
        apartment: g.apartment || null,
        messages: g.messages.map(m => ({
          role: m.role === 'user' ? 'user' : 'bot',
          content: m.content,
          tip: m.tip || '',
          source: m.source || '',
          sources: m.sources || [],
          suggested_questions: m.suggested_questions || [],
          law_notice: m.law_notice || '',
          message_id: m.message_id || null,
          feedback: m.feedback || null,
          contact_cards: m.contact_cards || [],
        })),
      };
    });
    currentLocalId = chatSessions[0].localId;
    document.getElementById('region-select').value = chatSessions[0].region;

    renderChatList(); renderMessages(); updateChatTitle();
  } catch (err) {
    console.error('대화 기록 복원 실패:', err);
    createNewSession();
  }
}

// ===== 세션 관리 =====
function createNewSession() {
  const region = document.getElementById('region-select').value;
  const session = {
    localId: 'new-' + (++localSeq),
    serverId: null,
    title: '새 대화',
    region,
    messages: [],
  };
  chatSessions.unshift(session);
  currentLocalId = session.localId;
  renderChatList(); renderMessages(); updateChatTitle();
}

function switchSession(localId) {
  currentLocalId = localId;
  const session = currentSession();
  if (session) document.getElementById('region-select').value = session.region;
  renderChatList(); renderMessages(); updateChatTitle();
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
  renderChatList(); renderMessages(); updateChatTitle();
}

function renderChatList() {
  const list = document.getElementById('chat-list');
  // 날짜별 그룹핑은 서버에서 created_at 을 안 보내므로 단순 목록 유지
  list.innerHTML = chatSessions.map(s => `
    <div class="chat-item ${s.localId === currentLocalId ? 'active' : ''}"
         onclick="switchSession('${s.localId}')">
      <span class="chat-item-icon">💬</span>
      <span class="chat-item-text">${escapeHtml(s.title)}</span>
      <button class="chat-item-delete" onclick="deleteSession('${s.localId}', event)" title="삭제">✕</button>
    </div>
  `).join('');
}

function updateChatTitle() {
  const session = currentSession();
  document.getElementById('chat-title').textContent = session ? session.title : '새 대화';
}

// ===== 지역 (숨김 셀렉트, 세션에서 관리) =====
document.getElementById('region-select').addEventListener('change', function () {
  const session = currentSession();
  if (session) session.region = this.value;
});

// ===== 메시지 렌더링 (리디자인) =====
const QUICK_ICONS = ['♻️', '🧴', '🍎', '🦴'];
const QUICK_TITLES = ['배달 용기 분리수거', '페트병 라벨', '음식물쓰레기 배출', '뼈다귀 분류'];
const QUICK_SUBS = ['어떻게 해야 하나요?', '꼭 떼야 하나요?', '방법 알려주세요', '음식물쓰레기인가요?'];

function renderMessages() {
  const container = document.getElementById('chat-messages');
  const session = currentSession();

  if (!session || session.messages.length === 0) {
    container.innerHTML = `
      <div class="welcome-message">
        <div class="welcome-icon-wrap"><span class="welcome-leaf">🌿</span></div>
        <h3>안녕하세요! 무엇이든 물어보세요</h3>
        <p>분리배출, 음식물쓰레기, 대형폐기물, 에너지 절약까지<br>우리 동네 환경 실천을 도와드려요</p>
        <div class="quick-grid">
          ${quickQuestions.map((q, i) => `<button class="quick-card" onclick="sendQuickQuestion(this.dataset.q)" data-q="${escapeHtml(q)}"><span class="quick-card-icon">${QUICK_ICONS[i] || '💬'}</span><span class="quick-card-text"><span class="quick-card-title">${QUICK_TITLES[i] || escapeHtml(q)}</span><span class="quick-card-sub">${QUICK_SUBS[i] || ''}</span></span></button>`).join('')}
        </div>
      </div>`;
    return;
  }

  container.innerHTML = session.messages.map(msg => {
    if (msg.role === 'user') {
      return `
        <div class="message user">
          <div class="message-avatar user-avatar-msg">${escapeHtml(CONFIG.userName[0] || 'U').toUpperCase()}</div>
          <div class="message-content">${escapeHtml(msg.content)}</div>
        </div>`;
    }

    // ── 실천 팁 ──
    const tipHtml = msg.tip
      ? `<div class="chat-tip-card">
           <div class="chat-tip-label">💡 실천 팁</div>
           <p class="chat-tip-text">${escapeHtml(msg.tip)}</p>
         </div>`
      : '';

    // ── 출처 (muted 텍스트 + 가운뎃점) ──
    const allSources = [];
    if (msg.source) {
      msg.source.split(',').forEach(s => { if (s.trim()) allSources.push(s.trim()); });
    } else if (msg.sources && msg.sources.length) {
      msg.sources.forEach(s => { if (s.title) allSources.push(s.title); });
    }
    // 단지 규정 출처도 합침
    const aptSources = (msg.sources || []).filter(s => s.source_level);
    aptSources.forEach(s => {
      const label = s.source_level === 'official' ? '관리사무소' : '입주민 제보';
      allSources.push(s.title + ' (' + label + ')');
    });
    const uniqueSources = [...new Set(allSources)];
    const sourcesHtml = uniqueSources.length
      ? `<div class="chat-source-row">출처 ${uniqueSources.map(s => `<span class="chat-source-item">${escapeHtml(s)}</span>`).join('<span class="chat-source-sep">·</span>')}</div>`
      : '';

    // ── 시행 예정 법령 안내 (카드형) ──
    const lawHtml = msg.law_notice
      ? `<div class="chat-law-notice-card">
           <div class="chat-law-notice-label">⚠️ 시행 예정 법령 안내</div>
           <p class="chat-law-notice-text">${escapeHtml(msg.law_notice.replace(/^⚠\s*/, ''))}</p>
         </div>`
      : '';

    // ── 연락처 카드 (관리사무소 · 지자체) ──
    const cardsHtml = (msg.contact_cards && msg.contact_cards.length)
      ? `<div class="chat-contact-cards">${msg.contact_cards.map(c => {
           const icon = c.type === 'local_gov' ? '🏛️' : '🏢';
           const subtitle = c.type === 'local_gov' ? (c.department || '지자체') : '관리사무소';
           const rows = [];
           if (c.phone) rows.push(`<div class="contact-card-row">📞 ${escapeHtml(c.phone)}</div>`);
           if (c.address) rows.push(`<div class="contact-card-row">📍 ${escapeHtml(c.address)}</div>`);
           if (c.hours) rows.push(`<div class="contact-card-row">🕐 ${escapeHtml(c.hours)}</div>`);
           return `<div class="contact-card">
             <div class="contact-card-head">
               <span class="contact-card-icon">${icon}</span>
               <div>
                 <div class="contact-card-title">${escapeHtml(c.title || subtitle)}</div>
                 <div class="contact-card-subtitle">${escapeHtml(subtitle)}</div>
               </div>
             </div>
             ${rows.join('')}
           </div>`;
         }).join('')}</div>`
      : '';

    // ── 추천 질문 ──
    const suggestHtml = (msg.suggested_questions && msg.suggested_questions.length)
      ? `<div class="chat-suggest">
           <div class="chat-suggest-label">이런 질문은 어떠세요?</div>
           <div class="chat-suggest-btns">${msg.suggested_questions.map(q =>
             `<button class="chat-suggest-btn" onclick="sendQuickQuestion(this.textContent)">${escapeHtml(q.question || q)}</button>`
           ).join('')}</div>
         </div>`
      : '';

    // ── 피드백 버튼 ──
    const feedbackHtml = msg.message_id
      ? `<div class="chat-feedback" data-mid="${msg.message_id}">
           <button class="chat-fb-btn${msg.feedback === 'positive' ? ' active' : ''}" data-val="positive" onclick="toggleFeedback(${msg.message_id},'positive',this)" title="좋아요">👍</button>
           <button class="chat-fb-btn${msg.feedback === 'negative' ? ' active' : ''}" data-val="negative" onclick="toggleFeedback(${msg.message_id},'negative',this)" title="싫어요">👎</button>
         </div>`
      : '';

    return `
      <div class="message bot">
        <div class="message-avatar bot-avatar-msg">🌿</div>
        <div class="message-content">
          <p class="chat-answer-text">${escapeHtml(msg.content)}</p>
          ${tipHtml}
          ${sourcesHtml}
          ${lawHtml}
          ${cardsHtml}
          ${feedbackHtml}
          ${suggestHtml}
        </div>
      </div>`;
  }).join('');

  container.scrollTop = container.scrollHeight;
}

// ===== 피드백 =====
async function toggleFeedback(messageId, value, btn) {
  const session = currentSession();
  if (!session) return;

  // 같은 값 재클릭 → 취소
  const msg = session.messages.find(m => m.message_id === messageId);
  const newVal = (msg && msg.feedback === value) ? null : value;

  try {
    const url = CONFIG.urls.feedback.replace('/0/', '/' + messageId + '/');
    const res = await postJson(url, { feedback: newVal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (msg) msg.feedback = newVal;
  } catch (err) {
    console.error('피드백 저장 실패:', err);
    return;
  }
  // 버튼 상태만 갱신 (전체 re-render 없이)
  const wrap = btn.closest('.chat-feedback');
  wrap.querySelectorAll('.chat-fb-btn').forEach(b => b.classList.remove('active'));
  if (newVal) btn.classList.add('active');
}

// ===== 전송 =====
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
    <div class="message-avatar bot-avatar-msg">🌿</div>
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
    const data = await res.json();

    if (session) {
      session.serverId = data.session_id;
      session.messages.push({
        role: 'bot',
        content: data.answer,
        tip: data.tip || '',
        source: data.source || '',
        sources: data.sources || [],
        suggested_questions: data.suggested_questions || [],
        law_notice: data.law_notice || '',
        message_id: data.message_id || null,
        feedback: null,
        contact_cards: data.contact_cards || [],
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
  loadPopularQuestions(true);
}

// ===== 인기 질문 =====
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

// ===== 사이드바 리사이즈 =====
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
restoreAllSessions();
loadPopularQuestions();
