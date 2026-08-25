"""Ecobot Django 프로젝트 전역 환경설정.

강사 자료 django_member_board_openai_chromadb_rag_final/config/settings.py 를
골격으로 하고, RAG 설정 블록만 3차 프로젝트 app/core/config.py 의 값으로
교체했습니다.

중요: rag/ 앱의 서비스 모듈들은 `from django.conf import settings` 로
이 파일의 모듈 레벨 상수를 읽습니다. 3차의 pydantic Settings 클래스가
가지고 있던 속성명을 **그대로** 유지해야 서비스 코드를 수정 없이 씁니다.
(RAG_TOP_K, EMBEDDING_BACKEND, INDEX_DIR ... 이름을 바꾸지 마십시오)
"""
from pathlib import Path
import os

from dotenv import load_dotenv

# settings.py 기준 두 단계 위를 프로젝트 루트로 지정합니다.
BASE_DIR = Path(__file__).resolve().parent.parent
# 프로젝트 루트의 .env 를 환경변수로 로딩합니다.
load_dotenv(BASE_DIR / ".env")


def _env_bool(key: str, default: str = "False") -> bool:
    return os.getenv(key, default).strip().lower() == "true"


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


# ─────────────────────────── 기본 ───────────────────────────

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-secret-key-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", "True")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 회원 · 거주 지역 프로필
    "members",
    # 분리배출 Q&A 커뮤니티 (RAG 근거로도 색인됨)
    "boards",
    # 법령/가이드 문서 + 청킹 · 임베딩 · FAISS · 답변 생성
    "rag",
    # 챗봇 대화방 · 대화 기록 · 질문 클러스터
    "chat",
    # 관리자 통계 대시보드
    "dashboard",
    # 4차 2R 추가분: 아파트 단지 · 3단계 회원 계층(입주민/관리사무소
    # 관리자/서비스 운영자). rag/chat 이 이 앱의 모델을 문자열 참조로
    # 가리키므로(apartments.Apartment) migrate 순서상 rag/chat 보다 먼저
    # 와야 한다.
    "apartments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ─────────────────────────── DB ───────────────────────────
# 3차의 DATABASE_URL(sqlalchemy URL) 대신 Django 형식으로 분해했습니다.
# 3차 트러블슈팅 1번(법령 원문 LONGTEXT 문제)은 Django 에서 자동 해결됩니다.
# Django 의 TextField 는 MySQL 백엔드에서 이미 LONGTEXT 로 생성되므로
# with_variant(LONGTEXT, "mysql") 트릭이 필요 없습니다.
#
# DB_ENGINE=sqlite3 이면 MySQL 없이 파일 DB 로 바로 실행됩니다 (퀵스타트).
# 3차 env.example 도 같은 이유로 sqlite 를 기본값으로 두고 있었습니다.

if os.getenv("DB_ENGINE", "mysql") == "sqlite3":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("DB_NAME", "ecora"),
            "USER": os.getenv("DB_USER", "app_user"),
            "PASSWORD": os.getenv("DB_PASSWORD", "app_password"),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# 3차 users 테이블은 passlib bcrypt($2b$...) 해시를 쓰고 있었습니다.
# 기존 계정을 살릴 경우 아래 해시어를 목록에 넣고 DB 값 앞에 "bcrypt$" 를
# 붙여야 Django 가 검증할 수 있습니다. 데모 계정만 다시 만들 거라면
# 이 블록은 지우고 Django 기본값(PBKDF2)만 쓰십시오.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.BCryptPasswordHasher",
]

AUTH_USER_MODEL = "members.Member"
LOGIN_URL = "members:login"
LOGIN_REDIRECT_URL = "chat:room"
LOGOUT_REDIRECT_URL = "home"
SESSION_COOKIE_AGE = 60 * 60 * 24  # 3차 ACCESS_TOKEN_EXPIRE_MINUTES=1440 과 동일
SESSION_SAVE_EVERY_REQUEST = True


# ─────────────────────────── 로케일 · 정적 ───────────────────────────

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ══════════════════════ RAG 설정 ══════════════════════
# 3차 app/core/config.py 의 Settings 클래스 속성을 그대로 옮긴 것입니다.
# rag/ 아래 서비스 모듈이 이 이름들을 참조하므로 변경하지 마십시오.

# 인덱싱할 문서 출처: "db"(rag.Document 테이블) / "files"(data/ 폴더 직접 읽기)
RAG_SOURCE = os.getenv("RAG_SOURCE", "db")

# 청킹: 한 청크의 최대 문자 수 / 인접 청크 간 겹침 문자 수
CHUNK_SIZE = _env_int("CHUNK_SIZE", 700)
CHUNK_OVERLAP = _env_int("CHUNK_OVERLAP", 100)

# 검색: 기본으로 가져올 유사 청크 개수
RAG_TOP_K = _env_int("RAG_TOP_K", 4)

# 문서 종류별 자리 배분(_apply_quota).
# 법령은 조문 수가 많아 청크 비중이 가이드를 90:10으로 압도하므로
# 지역 전용 / 전국 공통 / 법령 자리를 각각 보장합니다.
RAG_TOP_K_REGION = _env_int("RAG_TOP_K_REGION", 2)
RAG_TOP_K_COMMON = _env_int("RAG_TOP_K_COMMON", 1)
RAG_TOP_K_LAW = _env_int("RAG_TOP_K_LAW", 2)
# 지역 필터가 없을 때 쓰는 가이드 합계 (하위 호환)
RAG_TOP_K_GUIDE = _env_int("RAG_TOP_K_GUIDE", 3)
# 4차 2R 추가분: 단지 규정 전용 자리. 단지 결과가 실제로 있을 때만
# _apply_quota() 가 이 자리를 배분한다(조건부 슬롯) — 없으면 기존
# 지역/공통/법령 3분할과 완전히 동일하게 동작해 기존 지표를 보존한다.
RAG_TOP_K_APARTMENT = _env_int("RAG_TOP_K_APARTMENT", 2)

# 유사도 임계값. 미만이면 근거 없음으로 보고 LLM 을 호출하지 않습니다.
# ⚠️ 임베딩 백엔드를 바꾸면 rag/management/commands/measure_threshold.py 로
#    반드시 재측정해야 합니다. 모델마다 유사도 분포가 다릅니다.
RAG_MIN_SCORE = _env_float("RAG_MIN_SCORE", 0.3)
# hash 백엔드는 표면 문자열 일치만 잡아 점수 스케일이 훨씬 낮습니다.
RAG_MIN_SCORE_LOCAL = _env_float("RAG_MIN_SCORE_LOCAL", 0.05)

# ── 임베딩 백엔드: hash | local | gemini | openai ──
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "openai")
LOCAL_EMBEDDING_DIMENSION = _env_int("LOCAL_EMBEDDING_DIMENSION", 384)
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
GEMINI_EMBEDDING_DIMENSION = _env_int("GEMINI_EMBEDDING_DIMENSION", 768)
# 임베딩 API 는 요청 1건당 최대 100개까지 받습니다.
GEMINI_EMBEDDING_BATCH = _env_int("GEMINI_EMBEDDING_BATCH", 100)
GEMINI_BATCH_DELAY = _env_float("GEMINI_BATCH_DELAY", 2.0)
GEMINI_RETRY_WAIT = _env_int("GEMINI_RETRY_WAIT", 30)
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_EMBEDDING_DIMENSION = _env_int("OPENAI_EMBEDDING_DIMENSION", 1536)

# ── 답변 생성 LLM: gemini | openai ──
LLM_BACKEND = os.getenv("LLM_BACKEND", "openai")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# ⚠️ 강사 자료 settings.py 의 기본값 "gpt-5.6-luna" 는 존재하지 않는
#    모델명입니다. 그대로 두면 런타임에 죽으므로 실제 모델명을 씁니다.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── 경로 ──
# FAISS 인덱스(index.faiss) + 청크 메타(chunks.json) + 임베딩 캐시 저장 위치.
# 3차의 data/indexes/ 에 대응합니다. 강사 자료의 RAG_VECTOR_DB_DIR 과
# 같은 자리이지만, Chroma 가 아니라 FAISS 를 그대로 씁니다.
# (통과율 93.3% · 환각률 6.7% 수치가 FAISS + _apply_quota 로 측정된 값이라
#  벡터스토어를 교체하면 evals/ 의 3차례 측정 이력이 무효가 됩니다)
INDEX_DIR = BASE_DIR / "vector_db"
# 법령 원문 txt 폴더
LAWS_DIR = BASE_DIR / "data" / "laws"
# 지역별 · 공통 배출 가이드 폴더
GUIDE_DIR = BASE_DIR / "data" / "guide"
# RAG_SOURCE=files 일 때 추가로 읽는 임시 폴더
DOCS_DIR = BASE_DIR / "data" / "docs"

# ── 검색 파이프라인: legacy | langchain ──
# 3차에는 rebuild_index()/search()/ask() 와 rebuild_index_langchain()/
# search_langchain()/ask_langchain() 이 **함수 단위로 복제**되어 있었습니다.
# 같은 로직이 두 벌이면 _apply_quota() 하나를 고칠 때마다 두 곳을 확인해야
# 하고, 실제로 3차 코드에도 "어느 쪽이 최종 형태인지 확인 필요" 주석이
# 남아 있습니다.
#
# 4차에서는 복제를 없애고 이 설정으로 갈아끼웁니다. 갈아끼우는 지점은
# rag/service.py 의 _retrieve() 한 곳뿐이고, 필터·자리배분·답변 생성은
# 두 경로가 공유합니다.
#
# 기본값 legacy: 통과율 93.3% · 환각률 6.7% 가 이 경로로 측정된 수치라
# 재현 가능한 상태를 기본으로 둡니다. LangChain 경로로 바꿀 때는
# manage.py measure_threshold 로 임계값을 재확인하십시오.
RAG_PIPELINE = os.getenv("RAG_PIPELINE", "legacy")

# 질문 클러스터링 임계값. 이 값 이상이면 기존 클러스터에 편입합니다.
# (3차 routers/rag.py 의 SIMILARITY_THRESHOLD 상수를 설정으로 승격)
QUESTION_CLUSTER_THRESHOLD = _env_float("QUESTION_CLUSTER_THRESHOLD", 0.85)

# 4차 추가분: 검색 실패 시 "유사 질문 추천"용 임계값. 위 병합 임계값(0.85)
# 보다 낮게 잡아, 완전히 같진 않아도 비슷한 과거 질문까지 후보로 잡는다.
QUESTION_SUGGEST_THRESHOLD = _env_float("QUESTION_SUGGEST_THRESHOLD", 0.55)
QUESTION_SUGGEST_LIMIT = _env_int("QUESTION_SUGGEST_LIMIT", 3)
# 이 이상이면 "추천할 필요 없는, 사실상 같은 질문"으로 보고 제외한다.
QUESTION_SUGGEST_DEDUP_THRESHOLD = _env_float("QUESTION_SUGGEST_DEDUP_THRESHOLD", 0.92)

# 답변 생성 시 프롬프트에 넣을 최근 대화 턴 수
CHAT_HISTORY_TURNS = _env_int("CHAT_HISTORY_TURNS", 3)
