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
    const regionColors = ['#3B6D11', '#84C7A7', '#97B6E6'];
    container.innerHTML = data.map((r, i) => {
      const pct = total > 0 ? Math.round(r.count / total * 100) : 0;
      const color = regionColors[i % regionColors.length];
      return `<div style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
          <span>${escapeHtml(r.label)}</span><span style="color:#6B6B65">${r.count}건 (${pct}%)</span>
        </div>
        <div style="height:6px;background:#F0F0EC;border-radius:3px">
          <div style="width:${pct}%;height:100%;background:${color};border-radius:3px"></div>
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
      `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:0.5px solid #E2E2DC;font-size:13px">
        <span><strong style="color:#3B6D11;margin-right:6px">${i + 1}.</strong>${escapeHtml(q.question.length > 30 ? q.question.substring(0, 30) + '...' : q.question)}</span>
        <span style="color:#6B6B65;white-space:nowrap;margin-left:8px">${q.count}건</span>
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
    const len = data.length;
    container.innerHTML = `<div style="display:flex;align-items:flex-end;gap:12px;height:160px;padding:10px 0">
      ${data.map((d, i) => {
        const h = Math.max(d.count / maxCount * 130, 4);
        const t = len > 1 ? i / (len - 1) : 1;
        const r = Math.round(181 + (59 - 181) * t);
        const g = Math.round(201 + (109 - 201) * t);
        const b = Math.round(138 + (17 - 138) * t);
        return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px">
          <span style="font-size:11px;color:#6B6B65">${d.count}</span>
          <div style="width:100%;max-width:40px;height:${h}px;background:rgb(${r},${g},${b});border-radius:3px 3px 0 0"></div>
          <span style="font-size:11px;color:#9B9B95">${d.date}<br>${d.day}</span>
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
        ? `<span class="index-dot" style="background:#3B6D11"></span><span>인덱스 활성 (${data.total_chunks}개 청크)</span>`
        : `<span class="index-dot"></span><span>인덱스 없음</span>`;
    }
    // 문서 유형별 통계 카드
    const docs = data.documents || [];
    const lawCount = docs.filter(d => d.source_type === 'law').length;
    const guideCount = docs.filter(d => d.source_type === 'guide').length;
    const aptCount = docs.filter(d => d.source_type === 'apartment').length;
    const setEl = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    setEl('doc-stat-total', docs.length);
    setEl('doc-stat-law', lawCount);
    setEl('doc-stat-guide', guideCount);
    setEl('doc-stat-apartment', aptCount);

    const tbody = document.getElementById('doc-table-body');
    if (!docs.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="stat-placeholder">문서가 없습니다.</td></tr>';
      return;
    }
    const typeBadgeColors = {
      '법령': {bg:'#E6F1FB',color:'#185FA5'},
      '가이드': {bg:'#EAF3DE',color:'#3B6D11'},
      '단지 규정': {bg:'#FFF3E0',color:'#8B5E0B'},
      '사용자 문서': {bg:'#F0F0EC',color:'#5A5A55'},
    };
    tbody.innerHTML = docs.map(d => {
      const badge = typeBadgeColors[d.type_label] || {bg:'#F0F0EC',color:'#5A5A55'};
      // document_id 가 없는 건 seed_docs 가 폴더에서 심은 문서라 DB 행이
      // 없다 — 지울 pk 자체가 없으므로 삭제 버튼을 아예 안 보여준다.
      const deleteCell = d.document_id
        ? `<button class="btn-text" onclick="deleteAdminDocument(${d.document_id}, '${escapeHtml(d.title).replace(/'/g, "\\'")}')">삭제</button>`
        : '';
      return `<tr>
        <td>${escapeHtml(d.title)}</td>
        <td><span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500;background:${badge.bg};color:${badge.color}">${escapeHtml(d.type_label)}</span></td>
        <td>${escapeHtml(d.region_label)}</td>
        <td>${d.chunk_count}</td>
        <td style="text-align:right">${deleteCell}</td>
      </tr>`;
    }).join('');
  } catch (_) {}
}

// 4차 추가분: 대시보드 문서 관리 테이블에서 바로 삭제한다. 권한 판정은
// 서버(rag/views.py::DocumentDeleteView)가 최종적으로 다시 하므로,
// 여기서 버튼이 보였다고 해서 무조건 지워지는 건 아니다 — 예를 들어
// 관리사무소 관리자 소유가 아닌 단지 규정이면 서버가 404 로 막는다.
async function deleteAdminDocument(id, title) {
  if (!confirm(`'${title}' 문서를 삭제하고 색인에서 제거할까요?`)) return;
  // documentDeleteBase 는 "{% url 'rag:document_delete' 0 %}" 로 만든
  // "/rag/documents/0/delete/" 형태다 — pk=0 이 경로 끝이 아니라
  // "/delete/" 앞 중간 세그먼트라서 끝 앵커($)를 걸면 절대 안 잡힌다.
  // (처음엔 $ 를 걸었다가 항상 원본 그대로("...documents/0/delete/")
  // POST 돼서 삭제가 404 로 실패했다 — 그때 고친 흔적.)
  const url = CONFIG.urls.documentDeleteBase.replace('/0/', `/${id}/`);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
    });
    if (!res.ok && res.status !== 302) throw new Error(`HTTP ${res.status}`);
    loadAdminDocuments();
  } catch (err) {
    alert('삭제에 실패했습니다: ' + err.message);
  }
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
// (rag/views.py DocumentUploadView 주석 참고).
//
// 4차 추가분(여러 파일 업로드 + 범위 버그 수정): 예전엔 as_public=1 만
// 보내고 upload_scope 를 안 보내서, 서버가 기본값 "national" 로 채워
// 조용히 다 가이드/전국공통으로 색인됐다. rag:upload 가 이제 upload_
// scope 를 명시적으로 요구하므로(자동 기본값 제거) 파일을 고르면 바로
// 올리지 않고 범위 선택 패널을 먼저 보여준다. 여러 파일은 같은
// "source_file" 키로 각각 append 하면 서버가 request.FILES.getlist()
// 로 한 번에 받아 파일마다 Document 를 만든다.
let pendingUploadFiles = [];

function onUploadFilesSelected(input) {
  const fileList = Array.from(input.files || []);
  if (!fileList.length) return;
  pendingUploadFiles = fileList;

  document.getElementById('upload-file-list').textContent =
    fileList.length === 1
      ? `선택한 파일: ${fileList[0].name}`
      : `선택한 파일 ${fileList.length}개: ${fileList.map(f => f.name).join(', ')}`;

  document.getElementById('doc-upload-scope').value = '';
  document.getElementById('doc-upload-national-group').classList.add('hidden');
  document.getElementById('doc-upload-region-group').classList.add('hidden');
  document.getElementById('doc-upload-apartment-group').classList.add('hidden');
  document.getElementById('upload-scope-error').classList.add('hidden');
  document.getElementById('upload-scope-panel').classList.remove('hidden');
}

document.getElementById('doc-upload-scope').addEventListener('change', function () {
  const val = this.value;
  document.getElementById('doc-upload-national-group').classList.toggle('hidden', val !== 'national');
  document.getElementById('doc-upload-region-group').classList.toggle('hidden', val !== 'region');
  document.getElementById('doc-upload-apartment-group').classList.toggle('hidden', val !== 'apartment');
  document.getElementById('upload-scope-error').classList.add('hidden');
});

function cancelAdminUpload() {
  pendingUploadFiles = [];
  document.getElementById('file-input').value = '';
  document.getElementById('upload-scope-panel').classList.add('hidden');
}

async function submitAdminUpload() {
  if (!pendingUploadFiles.length) return;
  const scope = document.getElementById('doc-upload-scope').value;
  if (!scope) {
    document.getElementById('upload-scope-error').textContent = '범위를 선택해 주세요.';
    document.getElementById('upload-scope-error').classList.remove('hidden');
    return;
  }
  const region = document.getElementById('doc-upload-region').value;
  const targetApartment = document.getElementById('doc-upload-apartment').value;
  if (scope === 'region' && !region) {
    document.getElementById('upload-scope-error').textContent = '지역을 선택해 주세요.';
    document.getElementById('upload-scope-error').classList.remove('hidden');
    return;
  }
  if (scope === 'apartment' && !targetApartment) {
    document.getElementById('upload-scope-error').textContent = '아파트를 선택해 주세요.';
    document.getElementById('upload-scope-error').classList.remove('hidden');
    return;
  }

  const files = pendingUploadFiles;
  const progress = document.getElementById('upload-progress');
  const progressText = document.getElementById('upload-progress-text');
  document.getElementById('upload-scope-panel').classList.add('hidden');
  progress.classList.remove('hidden');
  progressText.textContent =
    files.length === 1
      ? `'${files[0].name}' 업로드 및 색인 중... (문서 양에 따라 수십 초 걸릴 수 있습니다)`
      : `${files.length}개 파일 업로드 및 색인 중... (문서 양에 따라 수십 초 걸릴 수 있습니다)`;

  const nationalDocType = document.getElementById('doc-upload-national-type').value;

  const form = new FormData();
  files.forEach(file => form.append('source_file', file));
  form.append('title', '');
  form.append('upload_scope', scope);
  if (scope === 'national') form.append('national_doc_type', nationalDocType);
  if (scope === 'region') form.append('region', region);
  if (scope === 'apartment') form.append('target_apartment', targetApartment);

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
    pendingUploadFiles = [];
    document.getElementById('file-input').value = '';
    setTimeout(() => progress.classList.add('hidden'), 2500);
  }
}

// ===== 피드백 통계 =====
async function loadFeedbackStats() {
  try {
    let url = CONFIG.urls.feedbackStats;
    const aptFilter = document.getElementById('feedback-apt-filter');
    if (aptFilter && aptFilter.value) {
      url += '?apartment=' + encodeURIComponent(aptFilter.value);
    }
    const res = await fetch(url);
    if (!res.ok) return;
    const d = await res.json();

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('fb-total', d.total.toLocaleString());
    set('fb-rate', d.rate + '%');
    set('fb-positive', d.positive.toLocaleString());
    set('fb-negative', d.negative.toLocaleString());

    // 만족도 바
    const barContainer = document.getElementById('feedback-bar-container');
    if (d.total === 0) {
      barContainer.innerHTML = '<p class="stat-placeholder">피드백 데이터가 없습니다.</p>';
    } else {
      const posRate = d.rate;
      const negRate = 100 - posRate;
      barContainer.innerHTML = `
        <div style="display:flex;border-radius:6px;overflow:hidden;height:28px;margin-bottom:8px">
          <div style="width:${posRate}%;background:#97C459;display:flex;align-items:center;justify-content:center">
            <span style="font-size:12px;font-weight:600;color:#173404">${posRate}%</span>
          </div>
          <div style="width:${negRate}%;background:#F09595;display:flex;align-items:center;justify-content:center">
            <span style="font-size:12px;font-weight:600;color:#501313">${negRate}%</span>
          </div>
        </div>
        <div style="display:flex;gap:16px;font-size:13px;color:#6B6B65">
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#97C459;margin-right:4px;vertical-align:middle"></span>좋아요 ${d.positive}건</span>
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#F09595;margin-right:4px;vertical-align:middle"></span>싫어요 ${d.negative}건</span>
        </div>`;
    }

    // 지역별 만족도
    const regionContainer = document.getElementById('feedback-region-container');
    if (!d.by_region || !d.by_region.length) {
      regionContainer.innerHTML = '<p class="stat-placeholder">지역별 피드백 데이터가 없습니다.</p>';
    } else {
      regionContainer.innerHTML = d.by_region.map(r => {
        const posW = r.total > 0 ? Math.max(r.rate, 3) : 0;
        const negW = r.total > 0 ? Math.max(100 - r.rate, 3) : 0;
        return `<div style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
            <span style="font-weight:500">${escapeHtml(r.label)}</span>
            <span style="color:#6B6B65">${r.rate}% (${r.total}건)</span>
          </div>
          <div style="display:flex;border-radius:4px;overflow:hidden;height:14px">
            <div style="width:${posW}%;background:#97C459"></div>
            <div style="width:${negW}%;background:#F09595"></div>
          </div>
        </div>`;
      }).join('');
    }

    // 최근 부정 피드백
    const negContainer = document.getElementById('feedback-neg-container');
    if (!d.recent_negative.length) {
      negContainer.innerHTML = '<p class="stat-placeholder">부정 피드백이 없습니다.</p>';
    } else {
      negContainer.innerHTML = d.recent_negative.map((item, i) => `
        <div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;${i < d.recent_negative.length - 1 ? 'border-bottom:0.5px solid #E2E2DC' : ''}">
          <span style="background:#FCEBEB;color:#A32D2D;font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;white-space:nowrap;margin-top:2px">👎 싫어요</span>
          <div style="flex:1;min-width:0">
            <p style="font-size:13px;margin:0 0 2px;font-weight:500">Q. ${escapeHtml(item.question)}</p>
            <p style="font-size:12px;color:#6B6B65;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">A. ${escapeHtml(item.answer)}</p>
          </div>
          <span style="font-size:11px;color:#9B9B95;white-space:nowrap;margin-top:2px">${escapeHtml(item.date)}</span>
        </div>
      `).join('');
    }
  } catch (_) {}
}

// ===== 초기 로드 (3차 loadAdminDashboard) =====
loadAdminStats();
loadAdminRegionStats();
loadAdminTopQuestions();
loadAdminDailyTrend();
loadAdminDocuments();
loadFeedbackStats();
