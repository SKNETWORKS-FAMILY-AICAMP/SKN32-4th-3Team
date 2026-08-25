"""RAG 오케스트레이터 — 청킹 · 임베딩 · 벡터 검색 · 답변 생성 조합.

3차 app/services/rag_service.py(599줄)의 이식 완료본입니다.

  - rebuild_index() : 문서 전체 → 청킹 → 임베딩 → FAISS 재구축
  - search()        : 질문과 유사한 청크 검색 (소유자 필터 + 유사도 임계값)
  - ask()           : 검색 결과를 근거로 답변 + 출처 반환

문서 출처는 .env 의 RAG_SOURCE 로 전환합니다.
  - "db"    : documents 테이블 (기본)
  - "files" : data/ 폴더의 txt/md/pdf (DB 없이 단독 테스트용)

공용 문서:
  법령(law)·가이드(guide)는 소유자와 무관하게 모든 사용자가 검색할 수
  있습니다. 사용자 업로드 문서(manual)는 본인 것만 검색됩니다.

■ 3차 대비 바뀐 곳 (전체 목록)
  1. _load_from_db()      SQLAlchemy 세션 → Django ORM.
                          content/summary 를 색인에서 제외 (rag/models.py
                          상단 주석의 JSON 혼입 재현 결과 참고)
  2. rebuild_index()      db 파라미터 제거 + RAG_PIPELINE 분기
  3. search()             임베딩+벡터검색 두 줄 → _retrieve() 한 줄
  4. ask()                반환의 "answer" 폴백을 3차 그대로 유지
  5. 복제 함수 3개 삭제   rebuild_index_langchain / search_langchain /
                          ask_langchain → RAG_PIPELINE 스위치로 통합
  나머지 함수는 3차 본문 그대로입니다.

■ LangChain 경로
    3차의 함수 복제(ask / ask_langchain)를 없애고 settings.RAG_PIPELINE
    스위치로 통합했습니다. 갈리는 지점은 _retrieve() 한 곳뿐입니다.
    자세한 근거는 _retrieve() 위 주석을 보십시오.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from . import chunking, embeddings, vector_store

# 소유자와 무관하게 전체 공개되는 문서 유형 (수집한 공공자료)
PUBLIC_SOURCE_TYPES = ("law", "guide")

# 프론트에 돌려줄 근거 미리보기 길이
SNIPPET_LENGTH = 140

# 근거가 없을 때 쓰는 문구. 프롬프트의 지시문과 같은 표현을 씁니다.
NO_ANSWER = "관련 자료를 찾을 수 없습니다"

# LangChain FAISS 벡터스토어 저장 경로 (legacy 의 index.faiss 와 별개 파일)
_LANGCHAIN_INDEX_DIR = "langchain"


def _effective_min_score(min_score: float | None) -> float:
    """유사도 임계값을 결정한다.

    백엔드마다 점수 스케일이 다르므로 같은 임계값을 쓸 수 없다.
      - hash   : 표면 문자열 일치만 잡아 0.05~0.15 수준 → 전용 임계값 사용
      - local  : sentence-transformers. 의미 기반이라 점수대가 높다
      - gemini : 0.3~0.7 수준
      - openai : 0.2~0.6 수준

    hash 를 제외한 나머지는 RAG_MIN_SCORE 를 쓰되,
    **백엔드를 바꾸면 manage.py measure_threshold 로 재측정해야 한다.**
    (모델마다 유사도 분포가 달라 같은 값이 맞지 않는다)
    """
    if min_score is not None:
        return min_score
    if settings.EMBEDDING_BACKEND.lower() == "hash":
        return settings.RAG_MIN_SCORE_LOCAL
    return settings.RAG_MIN_SCORE


# ══════════════════════════════════════════════════════════════
#  검색 경로 갈아끼우기 (3차의 함수 복제를 대체)
# ══════════════════════════════════════════════════════════════
#
# 3차 구조:
#     rebuild_index()  /  search()  /  ask()
#     rebuild_index_langchain()  /  search_langchain()  /  ask_langchain()
#
#     → 필터링(owner/region/min_score), _apply_quota(), _build_context(),
#       _build_sources(), _generate_answer() 가 두 경로에 **똑같이 복제**되어
#       있었습니다. 3차 코드의 주석도 "필터링·자리배분 규칙은 기존 search()와
#       완전히 동일하게 맞췄다"고 적고 있습니다 — 복제를 자각한 상태입니다.
#
# 4차 구조:
#     경로가 갈리는 지점은 "질문 → 후보 청크 목록" 단 하나뿐입니다.
#     그 한 군데만 _retrieve() 로 분리하고, 나머지는 전부 공유합니다.
#
#     ask() → search() → _retrieve() ─┬─ legacy    : vector_store.search()
#                                     └─ langchain : similarity_search_with_score()
#              ↓ (여기서부터 공유)
#           필터 → _apply_quota() → _build_context() → _generate_answer()
#
# ⚠️ 3차 vector_store.py 주석에 "distance_strategy=MAX_INNER_PRODUCT 로
#    맞췄고 점수가 소수점까지 동일하게 나왔다(재현·확인함)"고 적혀
#    있습니다. 그 검증을 믿고 임계값을 공유하되, 경로를 바꾼 뒤에는
#    manage.py measure_threshold 로 한 번 더 확인하십시오.


def _retrieve(query: str, fetch_k: int) -> list[dict]:
    """질문에 대한 후보 청크를 가져온다. 여기가 유일한 분기점이다.

    반환 형식은 두 경로가 동일하다.
        [{"content", "title", "document_id", "owner_id",
          "source_type", "region", "score"}, ...]
    """
    pipeline = settings.RAG_PIPELINE.lower()

    if pipeline == "langchain":
        from langchain_community.vectorstores import FAISS

        save_path = str(settings.INDEX_DIR / _LANGCHAIN_INDEX_DIR)
        embedding_fn = embeddings._get_langchain_embeddings_class()()
        store = FAISS.load_local(
            save_path,
            embedding_fn,
            allow_dangerous_deserialization=True,
        )
        return vector_store.search_with_langchain(store, query, fetch_k)

    if pipeline != "legacy":
        raise ValueError(
            f"RAG_PIPELINE 값이 잘못되었습니다: {pipeline!r} (legacy 또는 langchain)"
        )

    query_vector = embeddings.embed_query(query)
    return vector_store.search(query_vector, fetch_k)


# ─────────────────── 공개 API ───────────────────


def rebuild_index() -> dict:
    """문서 전체를 다시 인덱싱한다.

    전체 재구축 방식을 쓰는 이유:
      문서 수정·삭제 시 FAISS 와의 동기화 문제를 피하는 가장 단순한 방법이다.
      문서량이 적은 초기 단계에서는 몇 초면 끝나므로 증분 갱신은 추후 과제로 둔다.

    3차 대비 달라진 점
      - db 파라미터 제거 (Django 는 세션을 넘기지 않는다)
      - RAG_PIPELINE=langchain 이면 LangChain 벡터스토어를 만들어
        save_local() 한다. 3차 rebuild_index_langchain() 을 별도 함수로
        두지 않고 여기서 분기한다.
    """
    documents = _load_documents()

    if settings.RAG_PIPELINE.lower() == "langchain":
        store = vector_store.build_langchain_vectorstore(documents)
        save_path = str(settings.INDEX_DIR / _LANGCHAIN_INDEX_DIR)
        store.save_local(save_path)
        return {
            "documents": len(documents),
            "source": settings.RAG_SOURCE,
            "embedding_backend": settings.EMBEDDING_BACKEND,
            "pipeline": "langchain",
            "saved_to": save_path,
        }

    chunks = chunking.build_chunks(documents)

    vectors = embeddings.embed_documents([c["content"] for c in chunks])
    count = vector_store.rebuild(chunks, vectors, embeddings.get_dimension())

    return {
        "documents": len(documents),
        "indexed_chunks": count,
        "source": settings.RAG_SOURCE,
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "pipeline": "legacy",
    }


def search(
    query: str,
    top_k: int | None = None,
    owner_id: int | None = None,
    min_score: float | None = None,
    region: str | None = None,
    balanced: bool = False,
) -> list[dict]:
    """질문과 유사한 청크를 점수 순으로 반환한다.

    owner_id 를 넘기면 "본인 문서 + 공용 법령·가이드"만 남긴다.
    region 을 넘기면 "해당 지역 + common(공통)" 문서만 남긴다.
    min_score 미만인 결과는 근거로 삼기에 부족하다고 보고 제외한다.

    balanced=True 면 문서 종류별 자리를 배분한다. (답변 생성용)
    이때 개수는 top_k 가 아니라 RAG_TOP_K_GUIDE + RAG_TOP_K_LAW 로 정해진다.
    balanced=False 면 순수 유사도 순으로 top_k 개를 반환한다. (검색 품질 진단용)

    [3차 대비] 임베딩 + 벡터 검색 두 줄이 _retrieve() 한 줄이 됐다.
    이 치환 하나로 search_langchain() 이 필요 없어졌다.
    """
    top_k = top_k or settings.RAG_TOP_K
    min_score = _effective_min_score(min_score)

    # 소유자·지역·종류 필터를 거친 뒤에도 개수를 채우려면 넉넉히 가져와야 한다.
    fetch_k = max(
        top_k,
        settings.RAG_TOP_K_REGION
        + settings.RAG_TOP_K_COMMON
        + settings.RAG_TOP_K_LAW,
    ) * 25
    results = _retrieve(query, fetch_k)

    if owner_id is not None:
        results = [
            r for r in results
            if r.get("owner_id") == owner_id
            or r.get("source_type") in PUBLIC_SOURCE_TYPES
        ]

    # 지역 필터: 해당 지역 + 전국 공통 문서만 남긴다.
    # region 이 None 인 청크는 전국 공통으로 간주한다.
    # (예전 인덱스나 region 컬럼이 비어 있는 문서를 통째로 잃지 않기 위함)
    if region:
        results = [
            r for r in results
            if r.get("region") in (region, "common", None)
        ]

    # 유사도 임계값 (환각 방지 1차 장치)
    results = [r for r in results if r.get("score", 0.0) >= min_score]

    if balanced:
        return _apply_quota(results, region)

    return results[:top_k]


def _apply_quota(results: list[dict], region: str | None = None) -> list[dict]:
    """문서 종류별 자리를 배분해 지역·공통·법령이 함께 잡히도록 한다.

    자리를 나누는 이유
      법령은 조문 수가 많아(수백 개) 청크 비중에서 가이드를 압도한다.
      또 전국 공통 가이드(에너지·탄소중립·일회용품 등)가 늘어나면
      가이드 자리를 공통이 모두 차지해 정작 필요한 지역 문서가 밀려난다.
      실제로 "쓰레기 몇 시에 내놔요?" 질문에서 부산 배출시간 청크가
      검색 결과에 들어오지 못하는 문제가 있었다.

    그래서 지역 전용 / 전국 공통 / 법령에 각각 자리를 보장한다.
    한 그룹이 자리를 못 채우면 남은 자리는 다른 그룹으로 넘겨 낭비하지 않는다.

    region 이 없으면(전체 검색) 지역 구분이 무의미하므로
    가이드 전체를 하나로 묶어 배분한다.
    """
    law_quota = settings.RAG_TOP_K_LAW
    if law_quota <= 0 and settings.RAG_TOP_K_REGION <= 0:
        return results

    laws = [r for r in results if r.get("source_type") == "law"]
    guides = [r for r in results if r.get("source_type") != "law"]

    if region:
        region_quota = settings.RAG_TOP_K_REGION
        common_quota = settings.RAG_TOP_K_COMMON

        # 선택한 지역 전용 문서와 전국 공통 문서를 나눈다
        local = [r for r in guides if r.get("region") == region]
        common = [r for r in guides if r.get("region") != region]

        picked = local[:region_quota] + common[:common_quota] + laws[:law_quota]
        total = region_quota + common_quota + law_quota
    else:
        guide_quota = settings.RAG_TOP_K_GUIDE
        picked = guides[:guide_quota] + laws[:law_quota]
        total = guide_quota + law_quota

    # 남은 자리를 다른 그룹에서 채운다
    if len(picked) < total:
        chosen = {id(r) for r in picked}
        picked += [r for r in results if id(r) not in chosen][: total - len(picked)]

    # 중요한 근거가 앞에 오도록 점수 순으로 정렬해 반환
    return sorted(picked, key=lambda r: r.get("score", 0.0), reverse=True)


def ask(
    question: str,
    top_k: int | None = None,
    owner_id: int | None = None,
    region: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """검색된 문맥을 근거로 답변을 생성한다.

    반환 형식:
        {"answer": str, "law": str, "tip": str, "source": str,
         "sources": [{"document_id": int, "title": str, "snippet": str}, ...],
         "contexts": [str, ...]}

    history: [{"role": "user"|"assistant", "content": str}, ...] (오래된 순).
        "대화 흐름 유지" 기능용 - None이면 기존과 완전히 동일하게 동작.

    근거가 없으면 LLM 을 호출하지 않는다 (환각 방지 1차 장치).
    자료없음 대응률 100% 가 이 분기 덕분이므로 반드시 유지할 것.
    """
    results = search(question, top_k, owner_id, region=region, balanced=True)

    # 근거가 없으면 LLM을 호출하지 않는다. (환각 방지)
    if not results:
        return {
            "answer": "관련 문서를 찾을 수 없습니다. 질문을 조금 더 구체적으로 바꿔 보세요.",
            "tip": "",
            "source": "",
            "sources": [],
            "contexts": [],
        }

    sections = _generate_answer(question, _build_context(results), history)
    source_list = _build_sources(results)

    return {
        "answer": sections.get("answer", "") or sections.get("guide", ""),
        "law": sections.get("law", ""),
        "tip": sections.get("tip", ""),
        "source": ", ".join(dict.fromkeys(s["title"] for s in source_list)),
        "sources": source_list,
        # RAGAS 평가용 원문 청크.
        # chat 뷰의 응답 조립이 걸러내므로 프론트 응답에는 포함되지 않는다.
        "contexts": [r["content"] for r in results],
    }


# ─────────────────── 컨텍스트·출처 조립 ───────────────────


def _build_context(results: list[dict]) -> str:
    """검색된 청크를 LLM에 넘길 하나의 문자열로 조립한다.

    가이드와 법령을 나눠서 넘긴다. 그래야 LLM이
    "실천 방법(가이드) + 법적 근거(법령)" 두 층으로 답할 수 있다.

        ### 배출 가이드
        [[서울시] 분리배출 요령 품목별 분리배출 요령 > 종이류]
        ...본문...

        ### 관련 법령
        [자원순환기본법 제15조]
        ...본문...
    """
    guides: list[str] = []
    laws: list[str] = []

    for item in results:
        block = f"[{item.get('title', '제목 없음')}]\n{item['content']}"
        (laws if item.get("source_type") == "law" else guides).append(block)

    parts: list[str] = []
    if guides:
        parts.append("### 배출 가이드\n" + "\n\n".join(guides))
    if laws:
        parts.append("### 관련 법령\n" + "\n\n".join(laws))

    return "\n\n".join(parts)


# 문서 분류용 접두사만 제거 대상. 지역명 대괄호는 남겨야 한다.
_TAG_PREFIX = re.compile(r"^\[(가이드|법령|샘플)\]_?\s*")


def _clean_title(raw_title: str) -> str:
    """파일명 형태의 제목을 사람이 읽기 좋은 형태로 정리한다.

    예) [가이드]_환경부_공통_분리배출_기준 → 환경부 공통 분리배출 기준
        폐기물관리법_시행규칙              → 폐기물관리법 시행규칙

    주의: "[서울시] 분리배출 요령" 처럼 대괄호에 지역명이 담긴 제목은
    그대로 둔다. 지우면 답변 출처에서 어느 지역 기준인지 알 수 없게 되고,
    평가에서도 어느 지역 문서가 검색됐는지 판별할 수 없다.
    """
    title = _TAG_PREFIX.sub("", raw_title)   # [가이드]_ 등 분류 접두사만 제거
    title = title.replace("_", " ")          # 언더스코어 → 공백
    return title.strip() or raw_title


def _build_sources(results: list[dict]) -> list[dict]:
    """검색 결과를 프론트 ChatSource 형식으로 변환한다.

    제목 중복 제거 로직은 3차 트러블슈팅 3번(같은 문서명이 출처에 3번
    반복 표시)의 해법이므로 유지한다.
    """
    sources: list[dict] = []
    seen: set = set()

    for item in results:
        cleaned = _clean_title(item.get("title", "제목 없음"))
        if cleaned in seen:
            continue
        seen.add(cleaned)

        snippet = " ".join(item["content"].split())
        if len(snippet) > SNIPPET_LENGTH:
            snippet = snippet[:SNIPPET_LENGTH] + "…"

        sources.append(
            {
                "document_id": item.get("document_id"),
                "title": _clean_title(item.get("title", "제목 없음")),
                "snippet": snippet,
            }
        )

    return sources


# ─────────────────── 문서 공급 ───────────────────


def _load_documents() -> list[dict]:
    """인덱싱 대상 문서를 [{"id","owner_id","title","content","source_type","region"}, ...] 로 반환.

    ── 4차에서 files 모드의 의미가 바뀐 이유 ──
    3차의 files 모드는 "DB 없이 단독 테스트용"이었습니다. 4차는 Django 가
    항상 DB 를 갖고(퀵스타트도 sqlite), 사용자 업로드 문서는 DB 에
    저장됩니다. files 모드가 폴더만 읽으면 업로드 문서가 색인에서
    빠집니다 — 3차 admin 업로드 버그(파일은 폴더에, 색인은 DB 를 읽음)가
    **방향만 바뀌어 재발**하는 구조이고, 실제로 퀵스타트 실행 검증에서
    재발을 확인했습니다 (업로드 직후 검색에 안 잡힘).

    그래서 사용자 업로드(manual)는 **어느 모드에서든 DB 에서** 읽습니다.
        db    모드: DB 전체 (law + guide + manual)
        files 모드: 폴더 (law + guide) + DB (manual)
    """
    if settings.RAG_SOURCE.lower() == "files":
        return _load_from_files() + _load_from_db(only_manual=True)
    return _load_from_db()


def _load_from_db(only_manual: bool = False) -> list[dict]:
    """documents 테이블에서 문서를 읽는다.

    only_manual=True 면 사용자 업로드(manual)만 읽는다 — files 모드가
    폴더의 공용 문서에 DB 의 업로드 문서를 합칠 때 쓴다 (_load_documents).

    ── 3차 대비 달라진 점 ──
    1. SQLAlchemy 세션 → Django ORM. 호출부가 db 세션을 넘길 수도 있게
       하던 own_session 분기 전체가 사라진다.
    2. content_text 하나만 읽는다. 3차는 content_text/content/summary 세
       후보를 합쳤는데, 실제 값으로 재현해보니 사용자 문서에서 에디터
       JSON 이, 요약이 있으면 LLM 출력이 색인 본문에 섞여 들어갔다.
       (rag/models.py 상단 주석의 재현 표 참고)
    3. print → logging. management command 와 view 양쪽에서 호출된다.
    """
    from .models import Document, SourceType

    queryset = Document.objects.all()
    if only_manual:
        queryset = queryset.filter(source_type=SourceType.MANUAL)

    documents: list[dict] = []

    for row in queryset.iterator():
        text = (row.content_text or "").strip()
        if not text:
            continue

        documents.append(
            {
                "id": row.pk,
                "owner_id": row.owner_id,
                "title": row.title,
                "content": text,
                # Django CharField 는 이미 str 이라 3차의
                # getattr(source_type, "value", ...) enum 방어가 필요 없다.
                "source_type": row.source_type,
                # region 이 None 이거나 "common" 이면 전국 공통으로 취급된다.
                # (search() 의 지역 필터가 그렇게 읽는다)
                "region": row.region,
            }
        )

    if not documents and not only_manual:
        # only_manual(files 모드의 업로드 문서 읽기)에서는 0건이 정상이므로
        # 경고하지 않는다 — "seed_docs 를 실행하라"는 안내가 오해를 부른다.
        import logging

        logging.getLogger(__name__).warning(
            "documents 테이블에 인덱싱할 문서가 없습니다. "
            "python manage.py seed_docs 를 먼저 실행하십시오."
        )

    return documents


def _extract_region(filename: str) -> str:
    """파일명에서 지역 코드를 추출한다. (RAG_SOURCE=files 전용)

    ⚠️ 3차부터 알려진 불일치: seed 쪽은 전국 공통을 None 으로, 이 함수는
    "common" 문자열로 표기한다. search() 의 지역 필터가 두 값을 모두
    공통으로 취급하므로 동작에는 문제가 없지만, 표기 통일은 추후 과제다.
    """
    REGION_MAP = {
        "서울": "seoul",
        "천안": "cheonan",
        "부산남구": "busan_namgu",
        "부산": "busan_namgu",
        "세종": "sejong",
        "인천미추홀구": "incheon_michuhol",
        "미추홀": "incheon_michuhol",
        "제주": "jeju",
        "공통": "common",
        "환경부": "common",
    }
    for keyword, code in REGION_MAP.items():
        if keyword in filename:
            return code
    return "common"


def _load_from_files() -> list[dict]:
    """data/guide + data/docs + data/laws 폴더에서 문서를 읽는다.
    (RAG_SOURCE=files, DB 없이 테스트용)

    settings.GUIDE_DIR / DOCS_DIR / LAWS_DIR 를 참조한다.
    """
    supported = {".txt", ".md", ".pdf"}
    documents: list[dict] = []

    search_dirs = [settings.GUIDE_DIR, settings.DOCS_DIR, settings.LAWS_DIR]

    for folder in search_dirs:
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(folder.iterdir()):
            if not (path.is_file() and path.suffix.lower() in supported):
                continue

            text = _read_file(path)
            if not text.strip():
                print(f"[RAG] 텍스트를 추출하지 못했습니다: {path.name} (스캔 PDF면 OCR 필요)")
                continue

            stem = path.stem

            # 폴더 기반 source_type 자동 태깅
            if folder == settings.LAWS_DIR or stem.startswith("[법령]"):
                source_type = "law"
            elif folder == settings.GUIDE_DIR or stem.startswith("[가이드]"):
                source_type = "guide"
            else:
                source_type = "manual"

            documents.append(
                {
                    # 3차는 1부터 증가하는 합성 id 를 썼다. 4차의 files 모드는
                    # DB 의 manual 문서와 합쳐지므로 합성 id 가 실제 pk 와
                    # 충돌해 "출처 보기" 링크가 엉뚱한 문서를 가리킬 수 있다.
                    # 폴더 문서는 DB 상세 화면이 없으니 None 으로 둔다.
                    "id": None,
                    "owner_id": None,
                    "title": stem,
                    "content": text,
                    "source_type": source_type,
                    "region": _extract_region(stem),
                }
            )

    if not documents:
        print(f"[RAG] 문서를 찾지 못했습니다. 경로: {search_dirs}")
        print(f"      지원 형식: {', '.join(sorted(supported))}")

    return documents


def _read_file(path: Path) -> str:
    """확장자에 맞는 방식으로 텍스트를 추출한다.

    DocumentUploadView 도 업로드 파일의 평문 추출에 이 함수를 쓴다.
    """
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            print("[RAG] pypdf 가 설치되지 않았습니다.  pip install pypdf")
            return ""
        return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949")


# ─────────────────── 답변 생성 ───────────────────


def _generate_answer(question: str, context: str, history: list[dict] | None = None) -> dict:
    """컨텍스트를 근거로 답변을 생성한다.

    llm.answer_with_context() 가 있으면 사용하고,
    없으면 검색된 원문을 그대로 보여주는 대체 답변을 반환한다.

    history 하위 호환 분기는 3차의 팀 병렬 작업(gemini_service 구버전이
    history 를 안 받던 시기) 흔적이다. 4차는 llm.py 를 함께 이식하므로
    사실상 항상 첫 분기를 타지만, 방어 코드라 비용이 없어 유지한다.
    """
    try:
        from . import llm as gemini_service

        if hasattr(gemini_service, "answer_with_context"):
            try:
                return gemini_service.answer_with_context(question, context, history=history)
            except TypeError:
                return gemini_service.answer_with_context(question, context)
    except ImportError:
        pass

    return {
        "answer": f"[LLM 미연결 상태 · 검색 결과 원문]\n{context}",
        "tip": "",
    }


# ─────────────────── 파사드 ───────────────────


class RagService:
    """강사 자료의 호출 모양(RagService())을 맞추기 위한 얇은 파사드.

    상태를 들고 있지 않으므로 매번 새로 만들어도 비용이 없다.
    (FAISS 인덱스는 vector_store.search() 가 파일에서 그때그때 읽는다)
    """

    def rebuild(self) -> dict:
        return rebuild_index()

    def search(self, question: str, **kwargs) -> list[dict]:
        return search(question, **kwargs)

    def ask(self, question: str, **kwargs) -> dict:
        return ask(question, **kwargs)

    def index_exists(self) -> bool:
        return vector_store.index_exists()
