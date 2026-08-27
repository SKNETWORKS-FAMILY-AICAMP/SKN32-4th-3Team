# 🌿 EcoBot — AI 기반 생활환경 실천 안내 서비스

> 환경부 가이드, 지역 조례, 아파트 관리규약을 LLM과 연동하여  
> 분리배출·폐기물 처리에 대해 **근거 기반으로 답변**하는 아파트 맞춤형 AI 챗봇

**SKN32 4차 프로젝트 3팀**

🔗 [ecobotapt.com](https://ecobotapt.com)

---

## 주요 기능

### AI 챗봇
- 자연어 질의응답 (RAG 기반, 근거 + 법령 조항 인용)
- 서울·부산·인천 등 **10개 지역별 맞춤 답변**
- 소속 아파트 관리규약 반영
- 답변 불가 시 관리사무소·지자체 연락처 카드 제공
- 유사 질문 추천, 좋아요/싫어요 피드백

### 아파트 단지 시스템
- 단지 검색·가입 신청 → 관리자 승인 → 단지 규정 자동 연동
- apartment_id 완전 일치 필터로 **타 단지 규정 노출 차단**
- 승인 전 fail-closed 방식 (단지 컨텍스트 자체 비노출)

### 커뮤니티 게시판
- 글 작성, 댓글, 좋아요, 카테고리 분류
- 관리자 공지사항 등록, 게시글 비공개 처리

### 관리자 대시보드
- **최종관리자**: 질문 통계·단지 관리·문서 관리 (3탭), 지역별 만족도 필터
- **중간관리자**: 단지 규정 업로드, 입주민 승인, 커뮤니티 관리

### 신뢰성 확보
- **환각 방지**: 근거 없으면 LLM 미호출
- **법령 시행일 자동 분류**: 시행 전 조문 답변 근거 자동 제외 + 안내 카드 표시
- **Balanced Search**: 지역·공통·법령·단지 문서를 쿼터별 배분

---

## 기술 스택

| 구분 | 기술 |
|---|---|
| Frontend | HTML5, CSS3, Vanilla JavaScript, Fetch API |
| Backend | Django 5.x, Class-Based View, 6개 앱 구조 |
| AI/ML | Gemini 2.5 Flash, OpenAI Embeddings, FAISS, LangChain |
| Database | MySQL (운영) / SQLite (개발) |
| Infra | Python 3.12, Git/GitHub, dotenv |
| 배포 | Caddy → Gunicorn → Django (ecobotapt.com) |

---

## 프로젝트 구조

```
SKN32-4th-3Team/
├── config/          # Django 설정 (settings, urls)
├── members/         # 회원 (가입·로그인·프로필·탈퇴)
├── chat/            # AI 챗봇 (대화방·메시지·RAG 연동)
├── rag/             # RAG 파이프라인 (색인·검색·답변 생성·문서 관리)
├── boards/          # 커뮤니티 게시판
├── apartments/      # 아파트 단지·멤버십·규정·관리자
├── dashboard/       # 최종관리자 대시보드
├── evals/           # 검색 파이프라인 평가 도구
├── templates/       # HTML 템플릿
├── static/          # CSS·JS·이미지
├── data/            # 법령·가이드 원본 문서
│   ├── laws/
│   └── guide/
├── requirements.txt
├── requirements-quickstart.txt
└── .env.example
```

---

## 사용자 역할

| 역할 | 설명 |
|---|---|
| 일반 사용자 (Resident) | 챗봇 이용, 커뮤니티 참여, 단지 가입 신청 |
| 중간관리자 (Manager) | 단지 규정 관리, 입주민 승인, 커뮤니티 관리 |
| 최종관리자 (Admin) | 전체 서비스 관리, 법령·가이드 업로드, 질문 통계 분석 |

---

## 설치 및 실행

### 1. 클론 및 가상환경

```bash
git clone https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN32-4th-3Team.git
cd SKN32-4th-3Team

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 2. 의존성 설치

```bash
# 퀵스타트 (SQLite + hash 임베딩)
pip install -r requirements-quickstart.txt

# 실사용 (MySQL + OpenAI)
pip install -r requirements.txt
```

### 3. 환경변수 설정

```bash
copy .env.example .env   # Windows
cp .env.example .env     # macOS/Linux
```

**퀵스타트 설정** (API 키 없이 실행):
```env
DB_ENGINE=sqlite3
RAG_SOURCE=files
EMBEDDING_BACKEND=hash
LLM_BACKEND=openai
```

**실사용 설정** (MySQL + OpenAI):
```env
DB_ENGINE=mysql
DB_NAME=ecora
DB_USER=app_user
DB_PASSWORD=app_password
DB_HOST=127.0.0.1
DB_PORT=3306
RAG_SOURCE=db
EMBEDDING_BACKEND=openai
OPENAI_API_KEY=sk-...
LLM_BACKEND=openai
```

### 4. DB 초기화 및 실행

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_docs
python manage.py rag_reindex
python manage.py seed_apartments
python manage.py runserver
```

`http://127.0.0.1:8000` 접속

| 명령어 | 설명 |
|---|---|
| `migrate` | DB 테이블 생성 |
| `createsuperuser` | 최종관리자 계정 생성 |
| `seed_docs` | 법령·가이드 문서를 DB에 적재 |
| `rag_reindex` | FAISS 벡터 색인 빌드 |
| `seed_apartments` | 데모 단지 및 샘플 규정 생성 |

---

## 주요 페이지

| URL | 설명 |
|---|---|
| `/` | 랜딩 페이지 |
| `/chat/` | AI 챗봇 |
| `/boards/` | 커뮤니티 게시판 |
| `/members/mypage/` | 마이페이지 |
| `/apartments/search/` | 단지 검색·가입 |
| `/apartments/manager/` | 중간관리자 대시보드 |
| `/dashboard/` | 최종관리자 대시보드 |

---

## RAG 파이프라인

```
질문 입력 → OpenAI Embeddings 벡터화 → FAISS 유사 문서 검색
    → Balanced Search (지역 2 + 공통 1 + 법령 2 + 단지 2)
    → 근거 유무 판단 (없으면 LLM 미호출)
    → Gemini 2.5 Flash 답변 생성 (GUIDE / LAW / TIP 3섹션)
```

### 검색 파이프라인 비교 (100문항 평가)

| 지표 | Legacy | BM25 | Hybrid |
|---|---|---|---|
| 답변 통과율 | 88% | 94% | 91% |
| 환각률 | 8% | 8% | **6%** |
| 검색 R@5 | 0.61 | 0.71 | **0.73** |

→ **Hybrid 선택**: 환각률이 가장 낮아 서비스 신뢰성 우선

---

## 배포 구조

```
Internet → Caddy (:443, HTTPS) → Gunicorn (127.0.0.1:8000) → Django → MySQL / FAISS
```

- **도메인**: ecobotapt.com
- **정적 파일**: WhiteNoise (Caddy 직접 서빙 대신 단일 프로세스 구성)
- **torch 제외**: 서버 메모리 절약 (sentence-transformers 미사용)
- **Gunicorn timeout**: 180초 (RAG 답변 생성 대기)

---

## 팀원 및 역할

| 이름 | 역할 |
|---|---|
| 하정원 | Backend / AI — 백엔드 아키텍처, 권한·인증 체계, RAG 신뢰성, 문서 업로드 파이프라인 |
| 정세환 | Data / Search — 데이터 수집·정제, 지자체 민원 연결, 검색 파이프라인 실험 |
| 박수진 | Frontend / Dashboard — 프론트엔드 UI, 커뮤니티 게시판, 관리자 대시보드·통계 |
| 임정택 | Infra / Data — Django 마이그레이션, 데이터 추가 수집, 인프라·서버 배포 |
