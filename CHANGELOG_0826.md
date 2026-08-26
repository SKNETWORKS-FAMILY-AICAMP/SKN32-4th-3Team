# 변경사항 공유 (2026.08.26 — soojin 브랜치)

## 0. 실행 방법

### 최초 설정

```bash
git clone https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN32-4th-3Team.git
cd SKN32-4th-3Team
git checkout soojin

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-quickstart.txt

copy .env.example .env
```

`.env` 파일에서 아래 값 설정:

```env
DB_ENGINE=sqlite3
RAG_SOURCE=files
EMBEDDING_BACKEND=hash
LLM_BACKEND=openai
OPENAI_API_KEY=sk-...    # 없으면 답변 대신 "[LLM 미연결]" 표시 (검색·저장은 정상 동작)
```

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py rag_reindex
python manage.py runserver
```

`http://127.0.0.1:8000` 접속.

### 이미 세팅된 경우 (pull 후)

```bash
git pull origin soojin
python manage.py makemigrations boards
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_docs
python manage.py rag_reindex
python manage.py seed_apartments
python manage.py runserver
```

### 주요 URL

| URL | 설명 |
|---|---|
| `/` | 랜딩 페이지 |
| `/chat/` | 챗봇 |
| `/members/mypage/` | 마이페이지 (신규) |
| `/boards/` | 커뮤니티 게시판 |
| `/dashboard/` | 관리자 대시보드 (staff 계정만) |
| `/admin/` | Django 관리자 |

> 상세 실행 가이드는 `RUN.md` 참고

---

## 1. 마이페이지 통합 (신규)

기존 **프로필**(`/members/profile/`)과 **내 단지**(`/apartments/mine/`) 페이지를 하나의 **마이페이지**(`/members/mypage/`)로 통합했습니다.

### 구성 (탭 방식)

| 탭 | 내용 |
|---|---|
| 내 정보 | 아이디, 이름, 닉네임, 이메일, 거주 지역, 전화번호, 가입일 + 프로필 수정 버튼 |
| 내 단지 | 현재 단지·역할 배지, 소속 단지 목록(전환/나가기), 바로가기(단지 규정 등) |
| 활동 내역 | 작성 글·댓글·좋아요 통계 + 서브탭(내 글 / 내 댓글 / 좋아요) |
| 설정 | 기본 지역 변경, 프로필 수정, 회원 탈퇴 |

### 역할별 차이

- **일반 사용자(입주민)**: 기본 4탭
- **중간관리자**: 내 단지 탭에 파란색 안내 메모 표시 ("커뮤니티 게시글 비공개 처리, 댓글 삭제 등 단지 관리 기능 사용 가능")
- **최종관리자**: 노란색 안내 메모 + 바로가기에 관리자 승인 큐, 관리자 명단 추가

### 변경 파일

- `members/views.py` — `MyPageView` 추가
- `members/urls.py` — `mypage/` 경로 추가
- `templates/members/mypage.html` — 신규 생성
- `static/css/style.css` — `mp-*` prefix 스타일 추가

---

## 2. 챗봇 UI 리디자인

챗봇 화면(`/chat/`) 전면 개편.

### 변경 내용

- **사이드바**: 로고 + 새 대화 버튼, 아파트 배지(승인 단지 있을 때), 대화 목록, 사용자 정보(아바타/이름/역할), 하단 링크(커뮤니티, 마이페이지, 로그아웃)
- **웰컴 화면**: 🌿 아이콘 + 2×2 플렉스 카드형 빠른 질문
- **답변 렌더링**: 답변 텍스트 → 실천 팁(앰버 카드) → 출처(뮤트 텍스트 + 가운뎃점 구분) → 법령 안내(뮤트 한 줄) → 관련 질문 버튼

### 변경 파일

- `templates/chat/room.html` — 전면 재작성
- `static/js/chat.js` — 전면 재작성
- `static/css/style.css` — 사이드바·웰컴·메시지 스타일 전체 교체
- `chat/views.py` — `apartment_name`, `membership_role` context 추가

---

## 3. 중간관리자 게시판 관리 기능

### 추가된 기능

- **게시글 비공개/공개 토글**: 관리자가 부적절한 게시글을 비공개 처리 가능
- **관리자 댓글 삭제**: 관리자가 자기 단지 내 모든 댓글 삭제 가능
- **비공개 배지**: 목록·상세에서 비공개 게시글에 "비공개" 배지 표시

### 변경 파일

- `boards/views.py` — `BoardHideView` 추가, `CommentDeleteView` 관리자 권한 추가
- `boards/urls.py` — `<int:pk>/hide/` 경로 추가
- `boards/models.py` — `is_hidden`, `hidden_by`, `hidden_at` 필드 (마이그레이션 필요)
- `templates/boards/board_detail.html` — 비공개 버튼·배지, 관리자 댓글 삭제 버튼
- `templates/boards/board_list.html` — 비공개 배지

---

## 4. 네비게이션 통합

기존에 분리되어 있던 "프로필"과 "내 단지" 링크를 "마이페이지" 하나로 통합.

| 위치 | 변경 |
|---|---|
| 챗봇 사이드바 | 🏢 내 단지 + 👤 프로필 → 👤 마이페이지 |
| 랜딩 페이지 네비게이션 | 마이페이지 버튼 추가 |
| 랜딩 아바타 링크 | 프로필 → 마이페이지 |
| 아파트 _nav.html | 내 단지 → 마이페이지 |
| 프로필 수정 돌아가기 | 프로필 → 마이페이지 |
| 회원 탈퇴 취소 | 프로필 → 마이페이지 |

---

## 주의사항

### 마이그레이션 필요

Board 모델에 `is_hidden`, `hidden_by`, `hidden_at` 필드가 추가되었습니다. 아래 명령어 실행 필요:

```bash
python manage.py makemigrations boards
python manage.py migrate
```

### 기존 URL 유지

`/members/profile/`과 `/apartments/mine/`은 그대로 동작합니다. 다른 뷰에서의 redirect가 깨지지 않도록 삭제하지 않았습니다.
