# EcoBot — 아파트 분리배출 AI 챗봇 (Django)

SKN32 4차 프로젝트. 3차에서 FastAPI로 구현한 분리배출 RAG 챗봇을 Django로 마이그레이션하고, 아파트 단지 관리·커뮤니티 기능을 추가한 서비스입니다.

## 주요 기능

- **AI 챗봇** — RAG 기반 분리배출 질의응답 (대화 저장·복원, 지역별 답변, 인기 질문)
- **회원 시스템** — 회원가입·로그인·프로필·탈퇴, 지역 선택
- **아파트 단지** — 단지 검색·가입 신청, 관리자 승인, 단지별 규정 관리
- **커뮤니티** — 게시판 CRUD, 댓글, 좋아요, 관리자 비공개 처리
- **관리자 대시보드** — 질문 통계 / 단지 관리 / 문서 관리 (3탭)
- **중간관리자 대시보드** — 단지별 규정·입주민 관리
- **문서 관리** — 법령·가이드·단지 규정 업로드, FAISS 색인, 원문 보기

## 사용자 역할

| 역할 | 설명 |
|---|---|
| 일반 사용자 (RESIDENT) | 챗봇 이용, 커뮤니티 참여, 단지 가입 신청 |
| 중간관리자 (MANAGER) | 단지 규정 관리, 입주민 승인, 커뮤니티 관리 |
| 최종관리자 (superuser) | 전체 서비스 관리, 관리자 승인, 문서·색인 관리 |

## 기술 스택

- Python 3.10+ (3.12 권장)
- Django 5.x
- FAISS (벡터 검색)
- OpenAI API (임베딩 + LLM) 또는 Gemini
- SQLite (개발) / MySQL (운영)

---

## 퀵스타트 (Windows 기준)

MySQL이나 API 키 없이 SQLite + hash 임베딩으로 빠르게 실행하는 방법입니다.

### 1. 클론 및 가상환경

```bat
git clone <repository-url>
cd SKN32-4th-3Team

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-quickstart.txt
```

> **참고:** 프로젝트를 영문 경로(예: `C:\proj4`)에 두는 것을 권장합니다. 한글 경로에서 FAISS 관련 오류가 발생할 수 있습니다.

### 2. 환경변수 설정

```bat
copy .env.example .env
```

`.env` 파일을 열어 아래 값으로 수정합니다:

```env
DB_ENGINE=sqlite3
RAG_SOURCE=files
EMBEDDING_BACKEND=hash
LLM_BACKEND=openai
OPENAI_API_KEY=            # 비워두면 "[LLM 미연결]" 안내가 나옵니다 (정상)
```

### 3. DB 마이그레이션 및 초기 데이터

```bat
python manage.py makemigrations boards
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_docs
python manage.py rag_reindex
python manage.py seed_apartments
python manage.py runserver
```

`http://127.0.0.1:8000` 접속.

### 각 명령어 설명

| 명령어 | 설명 |
|---|---|
| `makemigrations boards` | boards 앱 마이그레이션 파일 생성 |
| `migrate` | DB 테이블 생성 |
| `createsuperuser` | 최종관리자 계정 생성 (이름·닉네임 입력) |
| `seed_docs` | `data/laws`, `data/guide` 폴더의 공용 문서를 DB에 적재 |
| `rag_reindex` | FAISS 색인 빌드 (법령 9개 + 가이드 15개) |
| `seed_apartments` | 데모 단지 2개 + 샘플 규정 1건 생성 |
| `runserver` | 개발 서버 실행 |

---

## 실사용 구성 (MySQL + OpenAI)

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

```bat
pip install -r requirements.txt
```

MySQL에 DB를 먼저 생성해야 합니다:

```sql
CREATE DATABASE ecora CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

이후 동일하게 `migrate` → `createsuperuser` → `seed_docs --reindex` → `seed_apartments` → `runserver`.

---

## 주요 페이지

| 주소 | 설명 |
|---|---|
| `/` | 랜딩 페이지 |
| `/members/signup/` | 회원가입 |
| `/chat/` | AI 챗봇 |
| `/boards/` | 커뮤니티 게시판 |
| `/members/profile/` | 마이페이지 (내 정보·활동 내역·설정) |
| `/apartments/search/` | 단지 검색·가입 신청 |
| `/apartments/manager/` | 중간관리자 대시보드 |
| `/dashboard/` | 최종관리자 대시보드 |
| `/rag/documents/` | 문서 목록·업로드 |
| `/guide/recycling/` 등 | 분리배출 가이드 9종 |
| `/admin/` | Django 관리자 |

---

## 프로젝트 구조

```
SKN32-4th-3Team/
├── config/          # Django 설정 (settings, urls)
├── members/         # 회원 (가입·로그인·프로필·탈퇴)
├── chat/            # AI 챗봇 (대화방·메시지·RAG 연동)
├── rag/             # RAG 파이프라인 (색인·검색·문서 관리)
├── boards/          # 커뮤니티 게시판
├── apartments/      # 아파트 단지·멤버십·규정·관리자
├── dashboard/       # 최종관리자 대시보드
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

## 참고

- **hash 임베딩**은 파이프라인 검증용입니다. 동의어 검색이나 "자료없음" 판정 정확도가 필요하면 `EMBEDDING_BACKEND=openai`를 사용하세요.
- **API 키 없이** 실행하면 챗봇이 검색·출처 조립까지만 동작하고 답변 문장은 "[LLM 미연결]" 안내로 대체됩니다.
- 임베딩 백엔드를 변경하면 `RAG_MIN_SCORE` 재측정이 필요합니다.
- `.env.example`에 전체 환경변수 목록과 설명이 있습니다.
