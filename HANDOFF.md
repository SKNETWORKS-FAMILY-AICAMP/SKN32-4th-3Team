# 인수인계 (다음 작업 시작점)

3차 Ecobot(FastAPI) → 4차 Django 이식 작업의 현재 상태입니다.
상세 실행법은 `RUN.md`, 이식 근거·대응표는 `MIGRATION_MAP.md` 를 보십시오.

---

## 1. 지금 상태 — 한 줄 요약

**코드 이식은 전부 끝났습니다** — 회원 · 챗봇 · RAG · 문서관리 ·
관리자 대시보드 · 게시판 · 평가 도구(evals · measure_threshold)까지.
남은 것은 openai 백엔드로 지표를 재측정해 3차 수치와 비교하는 일뿐입니다.
`RUN.md` A절(퀵스타트)대로 실행하면 3차와 같은 화면에서 실제로 대화가 됩니다.

| 영역 | 상태 |
|---|---|
| 회원가입 · 로그인 · 프로필 · 탈퇴 | ✅ |
| 챗봇 (대화 저장·복원·삭제, 지역별 답변, 인기 질문) | ✅ |
| RAG 색인 · 검색 · 자리배분 · 환각 방지 | ✅ |
| 문서 목록 · 업로드(즉시 색인) · 원문 · 삭제 | ✅ |
| seed_docs (폴더→DB, 멱등) | ✅ |
| 관리자 대시보드 (통계·차트·문서관리·재빌드) | ✅ |
| 랜딩 · 가이드 9종 · 서비스 소개 | ✅ |
| **게시판 (boards)** | ✅ CRUD·검색·지역필터·조회수·첨부·권한 전부 동작 |
| **evals (LLM 혼합 평가) · measure_threshold** | ✅ 이식·배선 검증 완료 — **지표 재측정만 남음 (openai 백엔드 필요)** |

---

## 2. 다음에 할 일 (우선순위 순)

### (1) 지표 재측정 — 이식 검증의 완결 (남은 것은 실행뿐)
evals 와 measure_threshold 는 3차 소스를 받아 **이식·배선 검증까지
끝났습니다** (아래 2-2절). 이제 실제 백엔드로 재측정만 하면 됩니다.

```bat
:: .env — EMBEDDING_BACKEND=openai, LLM_BACKEND=openai, OPENAI_API_KEY,
::         RAG_SOURCE=db (RUN.md B절 구성)
python manage.py seed_docs --reindex     :: openai 임베딩으로 색인 재구축
python manage.py measure_threshold       :: 임계값 분포 → RAG_MIN_SCORE 확인
python -m evals.run_eval_hybrid          :: 30문항 평가 (채점 API 호출 발생)
python -m evals.run_report               :: 리포트 생성
```

3차 지표(통과율 93.3% · 환각률 6.7% · 자료없음 대응률 100%)와 같은
값이 나오는지 비교하면 이식 검증이 완결됩니다.
**hash 임베딩으로는 재현되지 않습니다** (아래 4번) — 이번 세션의 hash
배선 검증에서도 관련/무관 점수 분포가 완전히 겹치는 것을 실측했습니다
(무관한 "대구 수성구" 질문이 0.49 로 모든 관련 질문보다 높았음).

### (2) 선택 — 게시글의 RAG 근거 편입 실험
boards CRUD 는 끝났지만, 게시글을 검색 근거로 색인하는 결정은
`boards/models.py` 상단 주석대로 **보류 상태**입니다. `_apply_quota()`
의 지역/공통/법령 3분할에 "커뮤니티" 그룹을 추가하면 기존 측정치가
무효가 되므로, (1)의 재측정으로 기준선을 먼저 확보한 뒤 별도
브랜치에서 지표를 비교하며 실험하십시오. 순서를 뒤집으면 안 됩니다.

---

## 2-1. 끝낸 것 ① — boards 게시판

`verify_boards.py` (루트, 20건) 로 언제든 재검증할 수 있습니다.
403 검증 시 traceback 로그가 보이는 것은 정상입니다 (아래 4번).

| 파일 | 내용 |
|---|---|
| `boards/views.py` | 제네릭 CBV 5종. 읽기 공개 · 쓰기 로그인 · 수정/삭제 본인만(타인 403) · 삭제 POST 전용 · 조회수 F 표현식 |
| `boards/forms.py` | 신규. 첨부 화이트리스트 + 10MB 제한 (rag/forms.py 관례). 첨부는 색인 대상 아님 |
| `boards/models.py` | `get_absolute_url` 만 추가 — 스키마 무관, `makemigrations --check` 통과 |
| `templates/boards/` 3종 | document_list 의 `guide-layout`·`docs-wrap`·`admin-table` 재사용. 지역 필터 + 검색 + 페이지네이션(필터 유지) |
| `static/css/style.css` | 끝 "4차 추가분" 에 board-filter/paging/textarea 만 추가 (기존 1,499줄 무수정) |
| `templates/chat/room.html` | 사이드바에 커뮤니티 링크 1줄 |

설계 결정 3건 (근거는 views.py docstring 에도 있음):
- **읽기 공개**: 랜딩·가이드가 공개인 것과 같은 결 — 눈팅 유입 → 가입 유도.
- **타인 수정·삭제는 403** (rag 의 404 와 다름): 게시글은 공개라 존재를
  숨길 게 없고, 숨기면 목록·상세와 모순됩니다.
- **작성 폼 지역 기본값 = 회원 프로필 region**: 챗봇 기본 지역과 같은 발상.

---

## 2-2. 끝낸 것 ② — evals · measure_threshold 이식

3차 저장소(`SKN32-3rd-3Team-main.zip`)를 받아 이식했습니다.
원칙은 MIGRATION_MAP A절과 동일 — **측정 로직 본문 무수정, import 만 교체.**
(측정 방법이 달라지면 3차 지표와의 비교가 무효가 되기 때문)

| 파일 | 수정 내역 |
|---|---|
| `evals/qa_set.json` | 그대로 복사 (30문항 · 4유형) |
| `evals/run_report.py` | **그대로 복사 — 수정 0줄.** 이전 인수인계가 "run_report 의 import 를 고치라"고 했지만 실제 app 의존은 run_eval_hybrid 쪽이었습니다 (run_report 는 결과 JSON 만 읽음) |
| `evals/run_eval_hybrid.py` | `django.setup()` 부트스트랩 추가 + import 2줄 교체: `app.core.config` → `django.conf`, `app.services.rag_service` → `rag.service` (별칭 `rag_service` 로 받아 호출부 무수정) |
| `rag/management/commands/measure_threshold.py` | 스텁 → 3차 본문 이식. 함수 5개(top_score/measure/print_table/suggest/main)·상수 3개(REGIONS/RELEVANT/IRRELEVANT)를 **AST 비교로 원본과 본문 동일 확인.** `handle()` 은 `main()` 을 부르기만 합니다. CRLF → LF 정규화 |

검증 (hash 백엔드 · sqlite · 880청크에서 실행):
- `manage.py measure_threshold` 가 지역 6곳 × 14질문 측정 → 표 출력 →
  권장값 판정 → `threshold_*.json` 저장(3차와 동일하게 INDEX_DIR 상위 =
  프로젝트 루트)까지 완주.
- `python -m evals.run_report` — 결과 파일이 없을 때 "[중단]" 안내 후
  exit 1 (3차와 동일 동작).
- run_eval_hybrid import(= django.setup 경유) 후 `grade()` 와 동일한
  호출 경로로 `rag_service.search(balanced=True)` 5건 반환(자리배분 동작),
  `ask()` 가 "[LLM 미연결]" 폴백 + 출처 4건 반환, `is_refusal()` 규칙 동작.
- 시그니처 호환 사전 확인: 3차 eval 이 부르는
  `search(question, owner_id, region, balanced)` /
  `search(q, top_k=1, owner_id, min_score=0.0, region)` /
  `ask(question, owner_id, region)` 모두 4차 `rag/service.py` 와 일치.

명칭 교정: 이전 문서들이 "evals (RAGAS)" 라고 불렀지만 3차 evals 는
RAGAS 가 아니라 **LLM + 규칙 혼합 평가**입니다 (run_eval_hybrid.py 헤더
명시, ragas 의존 없음). 문서 표기를 "LLM 혼합 평가" 로 통일했습니다.

실행 안내: 3차와 같은 실행법이 그대로 유효합니다 — 프로젝트 루트에서
`python -m evals.run_eval_hybrid`, `python -m evals.run_report`.
(management command 로 감싸지 않은 이유: run_report 는 인자로 결과
파일명을 받는 반복 분석 도구라 3차 사용법 유지가 더 자연스럽고,
django.setup() 부트스트랩으로 충분합니다)

---

## 3. 발표·제출 전 확인할 것

- `.env` 의 `EMBEDDING_BACKEND=openai`, `LLM_BACKEND=openai`, `RAG_SOURCE=db`
  로 두고 `seed_docs --reindex` 를 한 번 돌려야 실제 품질이 나옵니다.
- `DEBUG=False` 로 바꾸면 `python manage.py collectstatic` 이 필요합니다.
- 3차 README 의 데모 스크린샷과 현재 화면을 나란히 두면
  "디자인 유지 + 프레임워크 이식" 이 한눈에 보입니다.

---

## 4. 알아둘 함정 (재발 방지)

**hash 임베딩은 품질 지표에 쓸 수 없습니다.**
API 키 없이 돌리라고 넣어둔 백엔드입니다. 880청크 규모에서는 무관한
질문도 2-gram 우연 일치로 임계값 0.05를 넘어서, '자료없음 대응' 이
사실상 동작하지 않습니다. 실행 확인용으로만 쓰십시오.

**Windows 한글 경로 + FAISS.**
`vector_store.py` 는 `write_index()` 대신 메모리 버퍼
(`serialize_index`/`deserialize_index`)를 씁니다. 한글 경로에서
"Illegal byte sequence" 가 나던 문제를 고친 것이니 되돌리지 마십시오.
단 **langchain 파이프라인(`RAG_PIPELINE=langchain`)은 여전히 영문 경로가
필요합니다** (RUN.md D절).

**rag 앱의 URL name 은 `document` 입니다** (`document_detail` 아님).
템플릿에서 `{% url 'rag:document' doc.pk %}` 로 쓰십시오.

**테스트 스크립트를 쓸 때는** `ALLOWED_HOSTS` 에 `testserver` 를 넣어야
합니다. 403/404 를 검증하는 테스트는 정상 동작 중에도 traceback 로그를
출력하므로, 로그가 보인다고 실패가 아닙니다.

**문자열 치환으로 `urls.py` 를 수정할 때는 반드시 `assert` 를 거십시오.**
앵커가 안 맞으면 조용히 아무것도 안 바뀌고, `NoReverseMatch` 가
한참 뒤에 터집니다. 이번 세션에서 두 번 겪었습니다.


---

## 5. 4차 확장 — 시행법 분류 · 유사질문 추천 · 지역 인프라 정리

이식 완료 이후 첫 확장 라운드입니다. 논의된 확장 후보 6가지(BM25 하이브리드
검색, 리랭커, 지역 확장, 시행법 분류, 아파트 단지 필드, 유사질문 추천) 중
**검색 품질(BM25·리랭커)과 아파트 필드, 신규 지역 데이터는 다음 라운드로
미루고** 아래 3가지만 구현했습니다.

### (1) 시행법인지 분류

`data/laws/` 의 모든 법령·시행령·시행규칙·고시·훈령 파일은 2번째 줄에
`[시행 YYYY. M. D.] [문서종류 제N호, ..., 개정유형]` 헤더를 갖고 있습니다
(law.go.kr 원문 그대로). `rag/law_text.py` 의 `parse_law_header()` 가 이걸
정규식으로 파싱하고, `seed_docs.py` 가 `rag.Document` 의 `law_effective_date`
/ `law_doc_number` / `law_amendment_type` 필드에 채워 넣습니다.

`rag/service.py` 의 `_annotate_law_status()` 가 `search()` 안에서(재색인
없이, DB 조회 한 번으로) 법령 결과에 `law_is_current` 를 붙입니다.
**`ask()` 는 `law_is_current is False` 인 법령을 답변 근거(그라운딩)에서
아예 제외합니다** — LLM 이 아직 시행되지 않은 조문을 현재 규정인 것처럼
인용하지 못하게, 컨텍스트·출처(sources)·contexts 전부 "이미 시행 중인"
근거만으로 조립합니다(제외 대상이 있었다는 사실 자체는 버리지 않고
`_law_notice()` 가 "곧 이렇게 바뀝니다" 안내 문구로 만들어 `law_notice`
키로 내려보내고, 화면에는 `.response-law-notice` 배지로 뜹니다). 근거가
전부 제외되고 남는 게 없으면(=관련 법령이 있긴 한데 전부 시행 전) LLM을
아예 호출하지 않고 그 사실을 그대로 안내합니다. `search()` 자체는
건드리지 않았습니다 — 관리자 진단 검색(`rag/views.py`)은 여전히 전체
결과를 보여줘야 하므로, 이 제외는 `ask()` 안에서만 합니다.

이 판정은 **저장해두지 않고 요청마다 오늘 날짜와 비교**합니다
(`Document.is_currently_effective()`). 그래서 법이 실제로 시행되는
날이 지나면 재색인이나 별도 배치 작업 없이 바로 다음 질문부터 그
법이 근거로 편입됩니다 — `unittest.mock.patch("django.utils.timezone.localdate", ...)`
로 날짜를 미래로 돌려서 `False → True` 로 뒤집히는 것과, 그 즉시
`ask()` 의 그라운딩에 포함되는 것까지 확인했습니다(아래 실행 결과 참고).

**확인된 실제 사례**: `data/laws/폐기물관리법.txt` 는 `[시행 2027. 1. 8.]`
로, 이 문서를 쓴 시점(2026-08-25) 기준 **아직 시행 전**입니다. 나머지
8개 법령 파일은 전부 이미 시행 중입니다. `python manage.py seed_docs`
(재색인 불필요, DB 필드만 갱신)를 돌린 뒤
`Document.objects.get(source_key="law:폐기물관리법.txt").is_currently_effective()`
가 `False` 를 반환하는지로 확인할 수 있습니다.

### (2) 검색 실패 시 유사 질문 추천

`chat/services.py` 의 `assign_cluster()`(질문 임베딩을 기존
`QuestionCluster` 와 코사인 비교해 병합)와 같은 계산을,
`rag/service.py` 의 새 함수 `suggest_similar_questions()` 가 재사용합니다.
차이는 "임계값 이상 중 최고 1개에 편입"이 아니라 "더 낮은 임계값
(`QUESTION_SUGGEST_THRESHOLD`, 기본 0.55 — 병합 임계값 0.85보다 낮음)
이상을 유사도 순 최대 `QUESTION_SUGGEST_LIMIT`(기본 3)개 추천"입니다.
`ask()` 가 근거를 하나도 못 찾았을 때만 호출되고, 응답의
`suggested_questions` 키로 내려가 화면에 "이런 질문은 어떠세요?" 칩으로
뜹니다(클릭하면 `sendQuickQuestion()` 으로 바로 재질문).

`QuestionCluster` 테이블이 거의 비어 있으면(신규 설치 직후) 추천이 안
뜨는 게 정상입니다 — 질문이 쌓일수록 채워집니다.

### (3) 지역 인프라 정리 (신규 지역은 이번엔 추가 안 함)

지역 키워드 매핑(파일명/제목 → 지역 코드)이 `rag/service.py` 와
`seed_docs.py` 에 **서로 다른 내용으로** 중복돼 있던 걸
`members/models.py` 의 `REGION_FILENAME_KEYWORDS` 하나로 합쳤습니다.
두 파일은 이제 이걸 import 만 합니다.

**의도적으로 건드리지 않은 것**: `rag/management/commands/measure_threshold.py`
의 `REGIONS` 상수와 `evals/` 의 지역 라벨 딕셔너리. 위 2-2절에서 이
파일들을 3차 원본과 AST 비교로 본문 동일함을 검증했다고 명시했기
때문에, 이번 정리 범위에서 의도적으로 제외했습니다.

**새 지역을 추가할 때 손댈 파일 체크리스트** (실제 지역 데이터는 아직
미정 — 다음에 지역명이 정해지면 이 순서대로):

1. `members/models.py` `REGION_CHOICES` 에 코드 추가 → `makemigrations`
2. `members/models.py` `REGION_FILENAME_KEYWORDS` 에 파일명 키워드 추가
   (표기 변형 — 띄어쓰기 있음/없음, 축약형 — 도 같이 넣을 것)
3. `data/guide/[가이드]_<지역명>_분리배출_요령.txt` 작성 후
   `python manage.py seed_docs --reindex`
4. (선택, 그 지역까지 지표를 재측정하고 싶으면) `measure_threshold.py`
   의 `REGIONS` 와 `evals/run_eval_hybrid.py`/`run_report.py` 의 라벨
   딕셔너리를 수동으로 갱신 — AST 동일성 검증 대상이므로 바꾸는 순간
   "3차 원본과 본문 동일"이 깨진다는 걸 인지하고 진행할 것
5. `templates/home.html` 에 지역 타일 하나 추가 (하드코딩돼 있음)
6. (선택) `members/guides.py` 에 `region-<지역명>` 가이드 페이지 추가
   — 이 파일의 키(`region-busan`, `region-incheon` 등)는 `REGION_CHOICES`
   코드(`busan_namgu`, `incheon_michuhol`)와 표기가 다르므로 그대로 따라갈 것

### 새 마이그레이션

`rag.Document`(law_effective_date/law_doc_number/law_amendment_type),
`chat.ChatMessage`(suggested_questions/law_notice) — 배포 전
`python manage.py makemigrations && python manage.py migrate` 필요.
