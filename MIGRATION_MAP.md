# 3차 → 4차 이식 지도

`SKN32-3rd-3Team` 의 파일별 이동 위치와 필요한 수정을 정리한 문서입니다.

참고 자료: 강사 제공 `django_member_board_openai_chromadb_rag_final`
(구조 골격) + `django_fastapi_ai_integrated_bootstrap_cbv_final`
(CBV · Bootstrap 관례).

---

## 1. 이식 난이도별 분류

### A. 이미 완료 — import 문만 바뀐 기계적 이식 (1,003줄)

아래 4개 파일은 **본문 한 줄도 안 바뀌었습니다.** 치환 내역은 이것뿐입니다.

```
from app.core.config import settings   →  from django.conf import settings
chunk_service.        →  chunking.
embedding_service.    →  embeddings.
vector_store_service. →  vector_store.
gemini_service        →  llm
```

| 3차 | 4차 | 줄 수 |
|---|---|---|
| `app/services/chunk_service.py` | `rag/chunking.py` | 262 |
| `app/services/embedding_service.py` | `rag/embeddings.py` | 295 |
| `app/services/vector_store_service.py` | `rag/vector_store.py` | 178 |
| `app/services/gemini_service.py` | `rag/llm.py` | 238 |

이게 가능한 이유는 `config/settings.py` 에서 3차 `Settings` 클래스의
**속성명을 그대로 유지**했기 때문입니다. `RAG_TOP_K`, `EMBEDDING_BACKEND`,
`INDEX_DIR` 같은 이름을 바꾸면 이 4개 파일 전부를 손대야 합니다.

> 조문 단위 청킹, 별표 독립 분리(트러블슈팅 2번), 임베딩 디스크 캐시,
> LLM_BACKEND 스위치, IndexFlatIP + L2 정규화 코사인 유사도 —
> 3차의 기술 선택이 전부 그대로 살아 있습니다.

### B. 재작성 — **완료** (검증 결과는 6번 절)

| 3차 | 4차 | 상태 |
|---|---|---|
| `app/services/rag_service.py` (599줄) | `rag/service.py` | ✅ 이식 완료. 바뀐 곳은 `_load_from_db()` ORM 재작성, `_retrieve()` 분기, `rebuild_index()` 파이프라인 분기뿐. "그대로 유지" 대상 8개 함수는 **AST 수준 비교로 원본과 본문 동일함을 확인** |
| `app/routers/admin.py` (250줄) | `dashboard/views.py` + `rag/views.py` | ✅ 재작성 완료. 응답 JSON 키·형식은 3차와 동일 (static/app.js 재사용 가능). 통계 5종 + 문서목록 + 재빌드 + 진단검색 + **업로드(색인 누락 버그 수정)** |

`dashboard/views.py` 가 3차와 의도적으로 다르게 한 4곳(시간대 aware 처리,
COUNT DISTINCT 의 NULL 처리, daily-trend N+1 제거, 지역 라벨 사전 통합)은
파일 상단 주석에 근거와 함께 정리돼 있습니다.

### C. 프레임워크가 대체 — 삭제

| 3차 | 대체물 |
|---|---|
| `app/core/security.py` | `django.contrib.auth` (세션 + PBKDF2) |
| `app/services/auth_service.py` | `login()` / `UserCreationForm` |
| `app/database.py` | Django `DATABASES` 설정 |
| `app/schemas.py` | Django Form + `JsonResponse` |
| `app/main.py` 의 `ALTER TABLE` 블록 | Django migration |
| `app/main.py` 의 `@app.on_event("startup")` 2개 | 아래 4번 참고 |
| `app/routers/api.py` 의 문서 CRUD | 업로드 화면으로 대체 (3번 참고) |
| `run.sh` | `python manage.py runserver` |

### D. 위치만 이동

| 3차 | 4차 |
|---|---|
| `scripts/seed_docs.py` + `scripts/law_text.py` | `rag/management/commands/seed_docs.py` |
| `scripts/measure_threshold.py` | `rag/management/commands/measure_threshold.py` |
| `scripts/create_user.py` | 불필요 — `manage.py createsuperuser` |
| `data/laws/` (9개) · `data/guide/` (15개) | 그대로 |
| `evals/` | 그대로 (`run_report.py` 의 import 경로만 수정) |
| `static/style.css` (1,475줄) | `static/css/style.css` — 챗봇·대시보드 부분 유지 |
| `static/app.js` (900줄) | `static/js/chat.js` — 아래 5번 참고 |
| `산출물/` | 그대로 |

---

## 2. 모델 대응표

| 3차 (SQLAlchemy) | 4차 (Django) | 비고 |
|---|---|---|
| `User` | `members.Member(AbstractUser)` | `region` 필드 추가 |
| `Document` | `rag.Document` | `content` · `parent_id` · `summary` 제거, `source_file` 추가 |
| `QuestionCluster` | `rag.QuestionCluster` | 그대로 |
| `ChatLog` | `chat.ChatLog` | `cluster` FK 로 정식화 |
| `ChatMessage` | `chat.ChatMessage` + **`chat.ChatSession`** | 대화방을 FK 로 승격 |
| `SourceType(enum)` | `rag.SourceType(TextChoices)` | `manual`/`law`/`guide` 만 유지 |

각 모델 파일 상단에 필드를 남긴/제거한 근거를 주석으로 적어 두었습니다.

### `LongText` 트릭이 사라집니다

3차 `models.py` 의 이 줄이 필요 없어집니다.

```python
LongText = Text().with_variant(LONGTEXT, "mysql")
```

Django 의 `TextField` 는 MySQL 백엔드에서 이미 `LONGTEXT` 로 생성됩니다.
트러블슈팅 1번(법령 원문 261KB 가 `TEXT` 65,535바이트 한계를 넘어
"Incorrect string value" 로 잘리던 문제)이 자동 해결됩니다.

---

## 3. 확정된 결정 사항

### (1) 사용자 문서 RAG 시나리오 — **유지**

3차 초안의 "사용자 문서도 함께 검색" 시나리오를 유지합니다.
필요한 부품이 모두 살아 있습니다.

| 부품 | 상태 |
|---|---|
| `Document.owner` (FK, null 허용) | 유지 — 본인 문서 필터 키 |
| `Document.content_text` | 유지 — 색인되는 유일한 본문 |
| `source_type="manual"` | 유지 |
| `search()` 의 owner 필터 | 3차 코드 그대로 |

제거한 것은 시나리오가 아니라 **에디터 부품**입니다.
근거를 실제 데이터 흐름으로 확인했습니다.

`_load_from_db()` 의 후보 합치기 로직을 실제 저장값으로 재현한 결과:

| 문서 | 색인되는 내용 |
|---|---|
| 법령 (`content` = `content_text` = 평문) | `'법령본문'` — 중복 제거가 우연히 막아줌 |
| 가이드 (동일) | `'가이드본문'` |
| 사용자 문서 (`content` = 에디터 JSON) | `'평문 본문\n\n{"type":"doc","content":[...]}'` |
| 요약까지 있는 문서 | `'평문 본문\n\n{"type":"doc"}\n\nLLM이 생성한 요약문'` |

- `content`: 법령·가이드에서는 261KB 를 두 컬럼에 똑같이 저장하는
  순수 중복입니다. 사용자 문서에서는 **직렬화된 JSON 이 색인 본문에
  섞여 들어갑니다.** 기능이 아니라 결함입니다.
- `summary`: LLM 출력이 색인 본문에 이어 붙습니다. 모델 출력을 다음
  답변의 근거로 색인하면 환각이 근거로 승격됩니다. 대표 지표가
  환각률 6.7% 인 프로젝트에서 스스로 열어둘 경로가 아닙니다.
- `parent_id`: 문서 계층은 RAG 와 무관한 내비게이션 기능입니다.

**4차에서 추가되는 것**: 3차에는 문서를 만들 UI 경로가 아예 없어서
(회원가입 화면조차 없었음) 이 기능이 잠들어 있었습니다.
`rag/forms.py` 의 `DocumentUploadForm` 과 `DocumentUploadView` 를
붙여 살립니다. 아파트 관리사무소 공지문, 우리 동 배출 안내문을 올리면
법령·지역 가이드와 함께 근거로 잡힙니다.

#### 함께 고쳐야 하는 3차 버그

`POST /api/admin/upload` 는 업로드 파일을 `data/guide/` 폴더에
저장한 뒤 `rebuild_index()` 를 호출합니다. 그런데 `rebuild_index()` 는
`RAG_SOURCE=db` 일 때 `documents` 테이블을 읽습니다. 방금 올린 파일은
폴더에만 있고 테이블에는 없으므로 **색인되지 않습니다.**
응답의 `indexed_chunks: N` 은 기존 문서의 청크 수라서 사용자에게는
성공으로 보이고, `scripts.seed_docs` 를 다시 돌릴 때까지 조용히
무시됩니다. `env.example` 기본값이 `RAG_SOURCE=db` 이므로 실제로
이 경로였습니다.

4차는 파일 저장 → `Document` 레코드 생성 → `rebuild_index()` 순서로
바꿉니다. `rag/views.py` 의 `DocumentUploadView` 주석에 적어뒀습니다.

**퀵스타트 실행 검증에서 같은 계열 버그를 하나 더 잡았습니다**:
`RAG_SOURCE=files` 모드는 폴더만 읽으므로 DB 에 저장되는 업로드 문서가
색인에서 빠집니다 — 저장소 불일치가 방향만 바뀌어 재발한 구조.
사용자 업로드(manual)는 어느 모드에서든 DB 에서 읽도록
`_load_documents()` 를 수정했고, 실행으로 재검증했습니다
(files 모드에서 업로드 직후 검색 1위 확인).

### (2) `ChatSession` FK 승격 — 확정, 데이터 마이그레이션 없음

기존 대화 데이터를 살리지 않기로 했으므로 `session_id` 문자열 →
`ChatSession` 변환 마이그레이션이 필요 없습니다.
`LEGACY_SESSION_KEY` 예외 처리도 함께 삭제합니다.

### (3) 권한 판정 — `is_staff` 기반 확정

`Member.is_service_admin` 프로퍼티를 씁니다.
3차 `"admin" in user.email.lower()` 는 `badmin@example.com` 을
관리자로 오인하고 `admin@` 아닌 관리자를 놓쳤습니다.

### (4) LangChain — 함수 복제를 스위치로 통합

LangChain 이 4차의 직접 요구사항은 아니지만 3차에서 이어지는 전제로
깔려 있으므로 **경로 자체는 유지**합니다. 다만 3차의 구조는 그대로
가져가지 않습니다.

```
3차:  rebuild_index()          search()          ask()
      rebuild_index_langchain() search_langchain() ask_langchain()
      → 필터링 · _apply_quota() · _build_context() · _generate_answer()
        가 두 경로에 똑같이 복제됨
```

3차 코드 주석도 "필터링·자리배분 규칙은 기존 search()와 완전히 동일하게
맞췄다"고 적고 있습니다. 복제를 자각한 상태이고, 실제로
`rag_service.py` 상단에는 "어느 쪽이 최종 형태인지 확인 필요
(내일 논의)" 주석이 남아 있습니다.

```
4차:  settings.RAG_PIPELINE = "legacy" | "langchain"

      ask() → search() → _retrieve() ─┬─ legacy    : vector_store.search()
                                      └─ langchain : similarity_search_with_score()
               ↓ 여기부터 완전 공유
            필터 → _apply_quota() → _build_context() → _generate_answer()
```

경로가 갈리는 곳은 "질문 → 후보 청크 목록" 하나뿐이므로 `_retrieve()`
28줄로 분리했습니다. `_apply_quota()` 를 고칠 때 한 곳만 보면 되고,
두 경로가 같은 임계값 코드를 통과하므로 점수 스케일 일치도 자동으로
검증됩니다.

기본값을 `legacy` 로 둔 이유: 통과율 93.3% · 환각률 6.7% 가 이 경로로
측정된 수치입니다. 재현 가능한 상태를 기본으로 두고, LangChain 경로는
`.env` 한 줄로 전환해 시연할 수 있습니다.

> 3차 `vector_store.py` 주석에 "`distance_strategy=MAX_INNER_PRODUCT`
> 로 맞췄고 점수가 소수점까지 동일하게 나왔다(재현·확인함)"고 적혀
> 있습니다. 그 검증을 근거로 임계값을 공유하되, 경로를 바꾼 뒤에는
> `manage.py measure_threshold` 로 한 번 더 확인하십시오.

## 4. `main.py` 의 startup 훅 처리

3차는 서버 시작 시 두 가지를 자동 실행했습니다.

**`_migrate_clusters()`** — `cluster_id` 가 없는 옛 `ChatLog` 를 클러스터에
매칭. → 일회성 작업이므로 데이터 마이그레이션 또는 관리 명령으로
옮기는 게 맞습니다. 매 서버 재시작마다 전체 스캔할 이유가 없습니다.

**`_auto_rebuild_index()`** — 인덱스가 없거나 임베딩 차원이 바뀌면 자동
재빌드. → 편리하지만 Django 개발 서버는 코드 변경마다 리로드되므로
그때마다 임베딩 API 를 때립니다(비용). `manage.py rag_reindex` 를
명시적으로 부르는 쪽을 권합니다. 차원 불일치 검사는
`vector_store.search()` 안에 이미 있어서 친절한 에러가 나옵니다.

---

## 5. 프론트엔드 이식 범위

| 3차 `static/index.html` 섹션 | 4차 |
|---|---|
| `landing-page`, `guide-page`, `service-page` | Django 템플릿 (`base.html` 상속) |
| `login-page`, `signup-page` | Django Form + CBV |
| `chat-page` | 템플릿 + `static/js/chat.js` (기존 JS 재사용) |
| `admin-page` | 템플릿 + 기존 fetch 코드, 통계는 ORM |

`static/app.js` 의 fetch 경로만 새 URL 로 바꾸면 됩니다.

```
/api/chat              → {% url 'chat:ask' %}
/api/chat/sessions     → {% url 'chat:sessions' %}
/api/popular-questions → {% url 'chat:popular' %}
/api/admin/stats       → {% url 'dashboard:stats' %}
/api/rag/rebuild       → {% url 'rag:rebuild' %}
/api/me                → 불필요 (템플릿에서 request.user)
/api/auth/*            → 폼 POST 로 전환
```

**주의**: POST 요청에 CSRF 토큰이 필요합니다. `credentials: 'include'`
는 같은 오리진이라 필요 없어지고, 대신 `X-CSRFToken` 헤더를 붙여야
합니다.

---

## 6. 검증 상태

정적 검사에 더해, **hash 임베딩(API 키 불필요) + sqlite 로 실제 실행**해
end-to-end 로 검증했습니다. LLM 은 키가 없으면 "[LLM 미연결]" 폴백을
반환하도록 3차 코드가 이미 설계돼 있어 전체 흐름 실행이 가능합니다.

```
python manage.py check                     → no issues (0 silenced)
python manage.py makemigrations --dry-run  → 5개 앱 모델 정상 생성
```

### rag/service.py (9개 항목 통과)

| 검증 항목 | 확인 내용 |
|---|---|
| 원본 충실성 | "그대로 유지" 8개 함수를 AST 비교(docstring 제외) → 전부 본문 동일. `ask()` 도 0줄 차이 |
| rebuild_index | 문서 4건 → 청크 5건, pipeline=legacy |
| 소유자 필터 | A 의 manual 문서가 A 검색에는 잡히고 B 검색에는 안 잡힘 |
| 지역 필터 | seoul 검색에 부산 문서 미포함, busan_namgu 검색에 부산 문서 포함 |
| 자리 배분 | 법령과 토큰이 겹치는 질문에서 guide + law 두 층이 함께 반환 |
| 환각 방지 | 근거 없는 질문 → LLM 호출 없이 "관련 문서를 찾을 수 없습니다" |
| LLM 폴백 | 키 없음 → "[LLM 미연결]" + 출처 조립은 정상 동작 |
| 진단 모드 | balanced=False → 순수 유사도 내림차순 top_k |
| 파사드 | RagService().index_exists()/search() 동작 |

주의: 자리 배분은 **임계값을 통과한** 법령이 있을 때만 법령 자리를
보장합니다 (3차 설계 그대로 — 필터 순서: 임계값 → 배분). hash 백엔드는
표면 문자열 일치만 잡으므로 테스트 질문에 법령과 겹치는 토큰이 필요했고,
이는 코드가 아니라 hash 임베딩의 특성입니다.

### dashboard/views.py (6개 영역 통과)

| 검증 항목 | 확인 내용 |
|---|---|
| stats | 3차 SQLAlchemy 의미를 독립 계산한 기대값과 **완전 일치** (total/today/yesterday/today_diff/active_users/success_rate/week_change) |
| 활성 사용자 | user=None(익명) 로그가 COUNT DISTINCT 에서 제외됨 (3차 SQL 의미 유지) |
| top-questions | 클러스터 없으면 GROUP BY 폴백, 있으면 클러스터 우선 |
| region-stats | 지역별 집계 + 라벨 매핑 |
| daily-trend | 쿼리 1번으로 7일 집계, 빈 날짜 0 채움, 3차와 동일한 {date, day, count} 형식 |
| documents | 색인 메타(chunks.json) 기반 목록, `_clean_title` 재사용 확인 |
| 접근 제어 | 비로그인 → 로그인 페이지 302, 일반 회원 → 403 |

### rag/views.py (업로드 흐름 통과)

| 검증 항목 | 확인 내용 |
|---|---|
| **업로드 즉시 색인** | txt 업로드 → Document 생성 → 재색인 → **추가 조치 없이 바로 검색에 잡힘** (3차의 폴더-DB 단절 버그가 고쳐졌다는 증명) |
| 입력 검증 | .exe 확장자 400 거부, 빈 텍스트 400 + 레코드 미생성 |
| 열람 권한 | 남의 manual 문서 → 404 (존재 여부도 숨김), 본인 → 200 |
| 삭제 동기화 | 삭제 후 같은 질문 재검색 → 결과에서 사라짐 (트러블슈팅 4번 계열 방지) |
| 진단 검색 | JSON 본문 POST, {"count","results"} 형식, 일반 회원 403 |
| 재빌드/상태 | POST rebuild 200, GET status {"index_exists": true} |

### 완료된 이식 작업 (전체)

| 파일 | 내용 | 상태 |
|---|---|---|
| `chat/views.py` | ChatAskView(세션 확보→히스토리 조회→답변 저장→{session_id,answer,tip,source,sources}), 세션 목록·삭제, 인기 질문, SELECTABLE_REGIONS | ✅ |
| `members/views.py` + `forms.py` | 회원가입(자동로그인→chat:room)/로그인/로그아웃/프로필/프로필수정/탈퇴(비활성화)/가이드(GUIDE_DATA 9종 서버렌더링) | ✅ |
| `rag/management/commands/seed_docs.py` | law_text.py 이식, source_key upsert, stale 삭제, --reindex. 법령9+가이드15=24건 멱등 확인 | ✅ |
| 템플릿 | base.html / home.html(landing) / guide.html / members 5종 / chat/room.html / dashboard/index.html / rag 3종 | ✅ |
| `static/css/style.css` | 3차 1,475줄 + 4차 추가분(form-error·flash·docs-wrap 등) = 1,499줄 | ✅ |
| `static/js/chat.js` | localId/serverId 이원화, X-CSRFToken, escapeHtml XSS 수정, 세션 서버삭제, 말풍선·타이핑·인기질문·리사이즈 유지 | ✅ |
| `static/js/dashboard.js` | loadAdminStats/Region/Top/DailyTrend/Documents/rebuildIndex/switchAdminTab/handleFileUpload | ✅ |
| `evals/` | import 경로 수정 대기 (미작업) | ⬜ |

#### 버그 수정 이력 (3건)

| 버그 | 원인 | 수정 |
|---|---|---|
| 관리자 업로드 색인 누락 | 파일 저장 후 DB 레코드 없이 rebuild 호출 | 파일→레코드→rebuild 순서 변경 |
| files 모드 업로드 색인 누락 | _load_documents() 가 폴더만 읽음 | manual 문서는 항상 DB에서 추가 로드 |
| Windows 한글 경로 FAISS 오류 | write_index()가 한글 경로 거부 | serialize_index/deserialize_index 메모리 버퍼로 교체 |

python manage.py seed_docs --reindex   # 법령9+가이드15, 880청크
python manage.py runserver
```

MySQL 사용 시 `CREATE DATABASE ecora` 를 먼저 실행하십시오.
Django 는 데이터베이스 자체를 만들지 않습니다.
