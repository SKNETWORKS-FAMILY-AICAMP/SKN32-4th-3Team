# 실행 가이드

**Python 3.10 이상 필요, 3.12 권장** (전 과정을 3.12 + Django 5.2 에서
실행 검증). 3.9 이하는 이식 코드의 `str | None` 문법 때문에 서버가 뜨기
전에 죽습니다. 팀원 간 버전을 맞추려면 루트의 `.python-version` 을
쓰십시오.

## A. 퀵스타트 — MySQL · API 키 없이 5분 (Windows 기준)

sqlite 파일 DB + hash 임베딩(파이프라인 검증용) + data/ 폴더 직접 읽기
조합입니다. 챗봇 화면은 아직 없지만 회원가입 → 로그인 → 문서 업로드 →
즉시 검색, 그리고 관리자 대시보드 API 까지 동작합니다.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-quickstart.txt

copy .env.example .env
```

`.env` 에서 아래 4줄만 바꿉니다.

```env
DB_ENGINE=sqlite3
RAG_SOURCE=files
EMBEDDING_BACKEND=hash
LLM_BACKEND=openai        # 키가 없으면 답변 대신 "[LLM 미연결]" 안내가 나옵니다 (정상)
```

```bat
python manage.py migrate
:: 관리자 계정 — 이름(display_name)까지 물어봅니다
python manage.py createsuperuser
python manage.py rag_reindex          :: data/laws 9개 + data/guide 15개 색인
python manage.py runserver
```

`http://127.0.0.1:8000` 접속 후 확인할 수 있는 것 (3차 디자인 그대로):

| 주소 | 내용 |
|---|---|
| `/` | 랜딩 — 히어로 · 기능 카드 · 지원 지역 (3차 landing-page 이식) |
| `/guide/recycling/` 등 | 가이드 9종 (분리배출·음식물·에너지 + 지역 6종, 서버 렌더링) |
| `/service/` | 서비스 소개 |
| `/members/signup/` | 회원가입 (지역 선택) → 자동 로그인 → 챗봇 |
| `/chat/` | **챗봇** — 대화 저장·복원, 대화방 전환·삭제, 지역 드롭다운, 인기 질문 |
| `/rag/documents/` | 문서 목록 · 업로드(즉시 색인) · 근거 원문 보기 |
| `/members/profile/` | 프로필 · 수정(거주 지역 → 챗봇 기본값) · 탈퇴 |
| `/dashboard/` | 관리자 대시보드 — 통계 카드 · 지역 분포 · 인기 질문 · 일별 차트 · 문서 관리 |
| `/admin/` | Django 관리자 (전 모델 등록) |

챗봇 답변에서 "[LLM 미연결] OPENAI_API_KEY 설정 후 다시 질문하세요"가
나오는 것이 퀵스타트의 정상 동작입니다 — 검색·출처·대화 저장까지는 전부
실제로 돌고, 답변 문장 생성만 API 키가 필요합니다.

검색 품질 진단(관리자, JSON):

```bat
:: 브라우저 콘솔 또는 curl 로
curl -X POST http://127.0.0.1:8000/rag/search/ -H "Content-Type: application/json" ^
     -d "{\"query\": \"종이컵 어떻게 버려요\", \"top_k\": 4}" --cookie "sessionid=..."
```

### 퀵스타트의 한계 (의도된 것 — 실행하며 실측한 내용)

- **hash 임베딩은 표면 문자열 일치만** 잡습니다. "계란/달걀" 같은
  동의어를 못 찾는 게 정상입니다 — 3차 트러블슈팅 5·7번이 정확히 이
  한계에 대한 기록입니다. 파이프라인 배선 확인용이지 품질 확인용이
  아닙니다.
- **hash 모드에서는 '자료없음' 판정이 사실상 무력합니다.** 실측:
  880청크 코퍼스에서 "양자컴퓨터 큐비트" 같은 무관한 질의도 2-gram
  우연 일치(컴퓨/퓨터 ↔ 폐가전 가이드의 "컴퓨터")와 해시 충돌로
  0.05 임계값을 넘어 근거가 잡힙니다. 실서비스 백엔드(openai,
  임계값 0.3)에서는 정상 동작하며, **3차가 백엔드별 임계값을 분리하고
  "백엔드를 바꾸면 재측정"을 요구한 이유가 바로 이것**입니다.
- LLM 키가 없으면 근거 검색·출처 조립까지만 동작하고 답변 문장은
  "[LLM 미연결]" 안내로 대체됩니다.
- 챗봇 화면(`/chat/`)과 프로필 화면은 아직 스텁입니다. 접근하면 500
  (NotImplementedError) — 남은 이식 작업입니다.

### 실행 검증 이력

이 문서의 A 절차를 그대로 자동화해 통과시켰습니다:
화면 3종 200 → 회원가입(부산 남구) → 자동 로그인 → txt 업로드 →
**재색인 조작 없이 즉시 검색 1위** → "쓰레기 몇 시에 내놔요" 질문에
부산 근거 포함(자리 배분 동작) → 임계값 차단 → 대시보드 권한
(일반 403 / 관리자 200) → 로그아웃. 최종 상태: 26문서 / 881청크.

검증 중 잡아서 고친 버그 1건: RAG_SOURCE=files 모드가 폴더만 읽어
**업로드 문서가 색인에서 빠지는** 문제 (3차 admin 업로드 버그가 방향만
바뀌어 재발한 구조). 사용자 업로드(manual)는 어느 모드에서든 DB 에서
읽도록 `_load_documents()` 를 고쳤습니다.

## B. 실사용 구성 — 3차와 동일한 품질

```env
DB_ENGINE=mysql            # + DB_NAME/USER/PASSWORD (MySQL 에 ecora DB 필요)
RAG_SOURCE=db              # documents 테이블에서 읽기
EMBEDDING_BACKEND=openai   # 3차 최종 구성 (1536차원)
OPENAI_API_KEY=sk-...
LLM_BACKEND=openai
```

```bat
pip install -r requirements.txt
python manage.py migrate
python manage.py rag_reindex        # 임베딩 API 호출 발생 (디스크 캐시 있음)
```

주의:
- MySQL 데이터베이스는 직접 만들어야 합니다. 3차 `database.py` 의
  자동 생성(CREATE DATABASE IF NOT EXISTS)이 Django 에는 없습니다.
  ```sql
  CREATE DATABASE ecora CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  ```
- `RAG_SOURCE=db` 는 documents 테이블을 읽습니다. 공용 문서 적재:
  ```bat
  python manage.py seed_docs --reindex
  ```
  (법령 9 + 가이드 15 적재, 재실행 시 갱신만 — 멱등 확인됨)
- 임베딩 백엔드를 바꾸면 유사도 분포가 달라집니다. `RAG_MIN_SCORE` 를
  그대로 두지 말고 재측정하십시오 (`measure_threshold` 이식 후).

## C. 지금 동작하는 것 / 안 하는 것 요약

| 영역 | 상태 |
|---|---|
| 회원가입 · 로그인 · 프로필 · 탈퇴 | ✅ 동작 (3차 login-card 디자인) |
| **챗봇** (대화 저장·복원·삭제, 지역별 답변, 인기 질문) | ✅ 동작 |
| RAG 색인 · 검색 · 자리배분 · 환각 방지 | ✅ 동작 |
| 문서 목록 · 업로드(즉시 색인) · 원문 보기 · 삭제 | ✅ 동작 |
| seed_docs (폴더→DB 적재, 멱등) | ✅ 동작 |
| 관리자 대시보드 (통계 4카드 · 지역 · TOP5 · 일별 차트 · 문서 관리 · 재빌드 · 공용 업로드) | ✅ 동작 |
| 랜딩 · 가이드 9종 · 서비스 소개 | ✅ 동작 (3차 디자인 이식) |
| Django 관리자(/admin/) | ✅ 전 모델 등록 |
| 게시판(boards) | ⬜ 모델만 — 강사 자료 CBV 붙여넣기 대기 |
| evals(RAGAS) · measure_threshold | ⬜ import 경로 수정 대기 |

## D. 트러블슈팅

### `rag_reindex` 에서 "Illegal byte sequence ... index.faiss for writing"

경로에 **한글이 있을 때** faiss 가 내던 오류입니다. faiss 의
`write_index`/`read_index` 는 C++ 이 `fopen(const char*)` 으로 직접
파일을 여는데, 파이썬이 넘긴 UTF-8 경로를 한국어 Windows 의 C 런타임이
CP949 로 해석하다 실패합니다 (`C:\새 폴더\...` 에서 실제 재현).

**현재 코드는 이 문제를 우회하도록 수정돼 있습니다** — 파일 I/O 를
파이썬이 담당하고 faiss 에는 메모리 버퍼만 넘깁니다
(`serialize_index`/`deserialize_index`, `write_index` 와 바이트 포맷
동일 · 기존 index.faiss 호환). 한글 경로에서 rebuild + search 를 실행해
검증했습니다.

남은 예외 1건: `RAG_PIPELINE=langchain` 은 LangChain 내부가
`faiss.write_index` 를 직접 호출하므로 **여전히 영문 경로가 필요**
합니다. 어느 모드든, 경로 관련 잡음을 피하려면 프로젝트를 영문 경로
(예: `C:\proj4`)에 두는 것을 권합니다 — 모델 캐시 등 다른 네이티브
라이브러리도 같은 계열의 문제를 일으킬 수 있습니다.
