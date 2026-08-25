// 3차 static/app.js 의 admin 부분 이식본.
// 바뀐 것: fetch 경로(dashboard-config), POST 에 X-CSRFToken,
//          업로드가 실제 폼 필드(title·source_file·as_public)로 전송,
//          innerHTML 에 들어가는 서버 문자열 이스케이프(XSS).
// 렌더링(통계 카드·지역 막대·인기 질문·일별 차트·문서 표)은 3차 그대로.

const CONFIG = JSON.parse(document.getElementById('dashboard-config').textContent);

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}

// ===== 탭 전환 (3차 switchAdminTab) =====
function switchAdminTab(tabName) {
  document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.admin-tab[data-tab="${tabName}"]`).classList.add('active');
  document.querySelectorAll('.admin-content').forEach(c => c.classList.add('hidden'));
  document.getElementById(`tab-${tabName}`).classList.remove('hidden');
  if (tabName === 'documents') loadAdminDocuments();
}

// ===== 통계 (3차 loadAdminStats) =====
async function loadAdminStats() {
  try {
    const res = await fetch(CONFIG.urls.stats);
    if (!res.ok) return;
    const d = await res.json();
    document.getElementById('stat-total').textContent = d.total.toLocaleString();
    document.getElementById('stat-today').textContent = d.today.toLocaleString();
    document.getElementById('stat-users').textContent = d.active_users.toLocaleString();
    document.getElementById('stat-success').textContent = d.success_rate + '%';
    const diffEl = document.getElementById('stat-today-diff');
    if (diffEl) {
      const diff = d.today_diff;
      diffEl.textContent = diff > 0 ? `+${diff} vs 어제` : diff < 0 ? `${diff} vs 어제` : '어제와 동일';
    }
    const weekEl = document.getElementById('stat-week-change');
    if (weekEl) {
      const wc = d.week_change;
      weekEl.textContent = wc > 0 ? `+${wc}% vs 지난주` : wc < 0 ? `${wc}% vs 지난주` : '지난주와 동일';
    }
  } catch (_) {}
}

// ===== 지역 분포 (3차 loadAdminRegionStats) =====
async function loadAdminRegionStats() {
  try {
    const res = await fetch(CONFIG.urls.regionStats);
    if (!res.ok) return;
    const data = await res.json();
    const container = document.getElementById('region-stats-container');
    if (!data.length) { container.innerHTML = '<p class="stat-placeholder">질문 데이터가 없습니다.</p>'; return; }
    const total = data.reduce((s, r) => s + r.count, 0);
    const colors = ['#4A7C59', '#6B9E78', '#8FBC8F'];
    container.innerHTML = data.map((r, i) => {
      const pct = total > 0 ? Math.round(r.count / total * 100) : 0;
      return `<div style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:13px">
          <span>${escapeHtml(r.label)}</span><span>${r.count}건 (${pct}%)</span>
        </div>
        <div style="background:#eee;border-radius:4px;height:8px;overflow:hidden">
          <div style="width:${pct}%;height:100%;background:${colors[i % colors.length]};border-radius:4px"></div>
        </div>
      </div>`;
    }).join('');
  } catch (_) {}
}

// ===== 인기 질문 (3차 loadAdminTopQuestions) =====
async function loadAdminTopQuestions() {
  const container = document.getElementById('top-questions-container');
  try {
    const res = await fetch(CONFIG.urls.topQuestions + '?limit=5');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.length) { container.innerHTML = '<p class="stat-placeholder">질문 데이터가 없습니다.</p>'; return; }
    container.innerHTML = data.map((q, i) =>
      `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:13px">
        <span><strong>${i + 1}.</strong> ${escapeHtml(q.question.length > 30 ? q.question.substring(0, 30) + '...' : q.question)}</span>
        <span style="color:#888;white-space:nowrap;margin-left:8px">${q.count}건</span>
      </div>`
    ).join('');
  } catch (_) {
    container.innerHTML = '<p class="stat-placeholder">질문 데이터를 불러올 수 없습니다.</p>';
  }
}

// ===== 일별 추이 (3차 loadAdminDailyTrend) =====
async function loadAdminDailyTrend() {
  try {
    const res = await fetch(CONFIG.urls.dailyTrend + '?days=7');
    if (!res.ok) return;
    const data = await res.json();
    const container = document.getElementById('daily-chart-container');
    if (!data.length) { container.innerHTML = '<p class="stat-placeholder">데이터가 없습니다.</p>'; return; }
    const maxCount = Math.max(...data.map(d => d.count), 1);
    container.innerHTML = `<div style="display:flex;align-items:flex-end;gap:12px;height:160px;padding:10px 0">
      ${data.map(d => {
        const h = Math.max(d.count / maxCount * 130, 4);
        return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px">
          <span style="font-size:11px;color:#666">${d.count}</span>
          <div style="width:100%;max-width:40px;height:${h}px;background:#4A7C59;border-radius:4px 4px 0 0"></div>
          <span style="font-size:11px;color:#888">${d.date}<br>${d.day}</span>
        </div>`;
      }).join('')}
    </div>`;
  } catch (_) {}
}

// ===== 문서 목록 (3차 loadAdminDocuments) =====
async function loadAdminDocuments() {
  try {
    const res = await fetch(CONFIG.urls.documents);
    if (!res.ok) return;
    const data = await res.json();
    const statusEl = document.getElementById('index-status');
    if (statusEl) {
      statusEl.innerHTML = data.index_exists
        ? `<span class="index-dot" style="background:#4A7C59"></span><span>인덱스 활성 (${data.total_chunks}개 청크)</span>`
        : `<span class="index-dot" style="background:#ccc"></span><span>인덱스 없음</span>`;
    }
    const tbody = document.getElementById('doc-table-body');
    if (!data.documents || !data.documents.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="stat-placeholder">문서가 없습니다.</td></tr>';
      return;
    }
    tbody.innerHTML = data.documents.map(d =>
      `<tr><td>${escapeHtml(d.title)}</td><td>${escapeHtml(d.type_label)}</td><td>${escapeHtml(d.region_label)}</td><td>${d.chunk_count}</td></tr>`
    ).join('');
  } catch (_) {}
}

// ===== 인덱스 재빌드 (3차 rebuildIndex + CSRF) =====
async function rebuildIndex() {
  if (!confirm('인덱스를 재빌드하시겠습니까?')) return;
  try {
    const res = await fetch(CONFIG.urls.rebuild, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
    });
    if (!res.ok) throw new Error('재빌드 실패');
    const data = await res.json();
    alert(`인덱스 재빌드 완료 (${data.indexed_chunks || 0}개 청크)`);
    loadAdminDocuments();
  } catch (err) {
    alert('인덱스 재빌드에 실패했습니다: ' + err.message);
  }
}

// ===== 파일 업로드 =====
// 3차 handleFileUpload 는 파일만 보냈고(제목·지역 없음) 색인도 안 됐다
// (rag/views.py DocumentUploadView 주석 참고). 4차는 실제 폼 필드로 보낸다:
//   title = 파일명, as_public=1 → 관리자 업로드는 공용 가이드로 색인된다.
async function handleFileUpload(input) {
  const file = input.files[0];
  if (!file) return;
  const progress = document.getElementById('upload-progress');
  const progressText = document.getElementById('upload-progress-text');
  progress.classList.remove('hidden');
  progressText.textContent = `'${file.name}' 업로드 및 색인 중... (문서 양에 따라 수십 초 걸릴 수 있습니다)`;

  const form = new FormData();
  form.append('source_file', file);
  form.append('title', file.name.replace(/\.[^.]+$/, ''));
  form.append('as_public', '1');

  try {
    const res = await fetch(CONFIG.urls.upload, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
      body: form,
    });
    if (!res.ok && res.status !== 302) throw new Error(`HTTP ${res.status}`);
    progressText.textContent = '업로드 완료 — 색인 반영됨';
    loadAdminDocuments();
  } catch (err) {
    progressText.textContent = '업로드 실패: ' + err.message;
  } finally {
    input.value = '';
    setTimeout(() => progress.classList.add('hidden'), 2500);
  }
}

// ===== 초기 로드 (3차 loadAdminDashboard) =====
loadAdminStats();
loadAdminRegionStats();
loadAdminTopQuestions();
loadAdminDailyTrend();
loadAdminDocuments();
