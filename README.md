# 🌿 EcoBot — AI 기반 생활환경 실천 안내 서비스

> 환경부 가이드, 지역 조례, 아파트 관리규약을 LLM과 연동하여  
> 분리배출·폐기물 처리에 대해 **근거 기반으로 답변**하는 아파트 맞춤형 AI 챗봇

**SKN32 4차 프로젝트 3팀**

🔗 [ecobotapt.com](https://ecobotapt.com)

---
## 팀원 및 역할

| 이름 | 역할 |
|---|---|
| 하정원 | Backend / AI — 백엔드 아키텍처, 권한·인증 체계, RAG 신뢰성, 문서 업로드 파이프라인 |
| 정세환 | Data / Search — 데이터 수집·정제, 지자체 민원 연결, 검색 파이프라인 실험 |
| 박수진 | Frontend / Dashboard — 프론트엔드 UI, 커뮤니티 게시판, 관리자 대시보드·통계 |
| 임정택 | Infra / Data — Django 마이그레이션, 데이터 추가 수집, 인프라·서버 배포 |

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
├── maintenance/     # 운영 유지보수 (고아 업로드 파일 정리, 모델 없음)
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

전체 절차·트러블슈팅은 **[docs/deploy.md](docs/deploy.md)** 를 보십시오.

| 파일 | 용도 |
|---|---|
| `requirements-prod.txt` | 운영 의존성 (torch 계열 제외, gunicorn·whitenoise 추가) |
| `.env.production.example` | 운영 환경변수 예시 |
| `deploy/gunicorn.conf.py` | WSGI 서버 설정 |
| `deploy/ecobot.service.in` | systemd 유닛 **템플릿** (설치 시 경로·계정 치환) |
| `deploy/Caddyfile.ecobotapt` | Caddy 사이트 블록 |
| `deploy/install-system.sh` | root 권한이 필요한 작업 (apt·MySQL·systemd·Caddy·DDNS) |
| `deploy/ddns-cloudflare.*` | 공인 IP 변경 시 A 레코드 자동 갱신 |
| `deploy/ecobot-reindex.*` | 문서 변경 시 백그라운드 재색인 |
| `deploy/ecobot-cleanup.*` | 고아 업로드 파일 주간 정리 (선택) |
| `docs/decisions/0001-*.md` | 구성 결정 배경과 대가 |

배포 전 반드시 확인할 것:

- 유닛은 `.in` 템플릿입니다 — `cp` 가 아니라 `install-system.sh` 로 설치하십시오
- `collectstatic` 실행 — 빠뜨리면 **모든 페이지가 500** 입니다
- `.env` 의 `DJANGO_DEBUG=False`, `DJANGO_BEHIND_PROXY=True`
- OpenAI 대시보드에서 **월 사용 한도** 설정 (회원가입이 열려 있고 rate limit 이 없습니다)

---

## 참고

- **hash 임베딩**은 파이프라인 검증용입니다. 동의어 검색이나 "자료없음" 판정 정확도가 필요하면 `EMBEDDING_BACKEND=openai`를 사용하세요.
- **API 키 없이** 실행하면 챗봇이 검색·출처 조립까지만 동작하고 답변 문장은 "[LLM 미연결]" 안내로 대체됩니다.
- 임베딩 백엔드를 변경하면 `RAG_MIN_SCORE` 재측정이 필요합니다.
- `.env.example`에 전체 환경변수 목록과 설명이 있습니다.
