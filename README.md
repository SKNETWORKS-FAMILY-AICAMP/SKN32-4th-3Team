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

- Python 3.10+ (3.12 권장) — [버전 호환성](#파이썬-버전--팀원-환경) 참고
- Django 5.x
- FAISS (벡터 검색)
- OpenAI API (임베딩 + LLM) 또는 Gemini
- SQLite (개발) / MySQL (운영)

---

## 파이썬 버전 · 팀원 환경

**3.10 ~ 3.13 어디서든 동작합니다.** 3.11과 3.12에서 실제로 색인·기동까지
검증했습니다. 3.12 전용 문법은 쓰지 않았습니다.

- **3.9 이하는 안 됩니다** — 이식된 코드가 `str | None` 문법을 씁니다.
- `.python-version`은 3.12로 고정돼 있습니다. uv나 pyenv를 쓰면 팀원 환경에
  3.12가 없어도 자동으로 받아 맞춰 주므로, 그대로 두는 편이 버전이 갈리지
  않아 낫습니다.

### `mysqlclient` 설치가 실패한다면

`mysqlclient`는 C 확장이고 PyPI에 **Windows 휠만** 올라와 있습니다(2.2.8 기준).

| 환경 | 필요한 것 |
|---|---|
| Windows | 없음 — 휠로 바로 설치됩니다 |
| Linux | `sudo apt install default-libmysqlclient-dev` |
| macOS | `brew install mysql-client pkg-config` |

증상은 `Exception: Can not find valid pkg-config name.` 입니다.
**개발 중이라면 `DB_ENGINE=sqlite3`을 쓰면 `mysqlclient` 자체가 필요 없습니다**
(퀵스타트 경로). MySQL은 서버에서만 씁니다.

### uv 를 쓴다면

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

uv는 `python3.x-venv` 시스템 패키지 없이도 venv를 만들고, 없는 파이썬 버전은
직접 내려받습니다. 다만 위 `mysqlclient` 빌드 의존성은 uv로도 우회되지 않습니다.

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
├── maintenance/     # 운영 유지보수 (고아 업로드 파일 정리, 모델 없음)
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

## 배포

운영 서비스는 **https://ecobotapt.com** 입니다.
전체 절차·트러블슈팅은 **[docs/deploy.md](docs/deploy.md)** 를 보십시오.

```
Caddy (443) ──▶ gunicorn (127.0.0.1:8000) ──▶ Django ──▶ MySQL + FAISS
```

| 파일 | 용도 |
|---|---|
| `requirements-prod.txt` | 운영 의존성 (torch 계열 제외, gunicorn·whitenoise 추가) |
| `.env.production.example` | 운영 환경변수 예시 |
| `deploy/gunicorn.conf.py` | WSGI 서버 설정 |
| `deploy/ecobot.service` | systemd 유닛 |
| `deploy/Caddyfile.ecobotapt` | Caddy 사이트 블록 |
| `deploy/install-system.sh` | root 권한이 필요한 작업 (apt·MySQL·systemd·Caddy·DDNS) |
| `deploy/ddns-cloudflare.*` | 공인 IP 변경 시 A 레코드 자동 갱신 |
| `deploy/ecobot-reindex.*` | 문서 변경 시 백그라운드 재색인 |
| `deploy/ecobot-cleanup.*` | 고아 업로드 파일 주간 정리 (선택) |
| `docs/decisions/0001-*.md` | 구성 결정 배경과 대가 |

배포 전 반드시 확인할 것:

- `collectstatic` 실행 — 빠뜨리면 **모든 페이지가 500** 입니다
- `.env` 의 `DJANGO_DEBUG=False`, `DJANGO_BEHIND_PROXY=True`
- OpenAI 대시보드에서 **월 사용 한도** 설정 (회원가입이 열려 있고 rate limit 이 없습니다)

---

## 참고

- **hash 임베딩**은 파이프라인 검증용입니다. 동의어 검색이나 "자료없음" 판정 정확도가 필요하면 `EMBEDDING_BACKEND=openai`를 사용하세요.
- **API 키 없이** 실행하면 챗봇이 검색·출처 조립까지만 동작하고 답변 문장은 "[LLM 미연결]" 안내로 대체됩니다.
- 임베딩 백엔드를 변경하면 `RAG_MIN_SCORE` 재측정이 필요합니다.
- `.env.example`에 전체 환경변수 목록과 설명이 있습니다.
