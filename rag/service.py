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

import logging

from django.conf import settings

from . import bm25_store, chunking, embeddings, scoring, vector_store

logger = logging.getLogger(__name__)

# 소유자와 무관하게 전체 공개되는 문서 유형 (수집한 공공자료)
# 버그 수정(2R-2): "apartment" 가 빠져 있어서 owner_id 필터가 단지 규정을
# 전부 걸러내고 있었다 — 단지 규정 Document 는 owner=None 으로 만들어지는데
# (apartments/services.py:sync_rule_to_document), owner_id 필터는
# r["owner_id"] == owner_id (로그인한 실제 사용자 pk) 인지만 보고 그 외엔
# PUBLIC_SOURCE_TYPES 인지로 판정한다. law/guide 처럼 사람을 안 가리는
# 문서라 여기 넣어야 맞다 — 실제 노출 범위는 owner_id 가 아니라 뒤에 있는
# apartment_id fail-closed 필터와 Document.status=approved 색인 게이트가
# 담당한다.
PUBLIC_SOURCE_TYPES = ("law", "guide", "apartment")

# 프론트에 돌려줄 근거 미리보기 길이
SNIPPET_LENGTH = 140

# 근거가 없을 때 쓰는 문구. rag/llm.py 의 ANSWER_PROMPT 가 LLM 에게
# 답을 거부할 때 정확히 이 문구로만 답하라고 지시한다(규칙 2, 7) — ask()
# 가 생성된 답변이 이 문구로 시작하는지 검사해서 관리사무소 문의 안내로
# 바꿔치기하는 데 쓴다. LLM 프롬프트의 표현이 바뀌면 이 값도 같이
# 바꿔야 한다.
NO_ANSWER = "관련 정보를 찾을 수 없습니다"

# 단지 규정 문서 본문에서 관리사무소 전화번호로 보이는 패턴을 찾을 때 쓴다.
# 02-1234-5678(지역번호) / 010-1234-5678(휴대전화) / 1588-1234(대표번호)
# 형태를 모두 잡는다.
_PHONE_RE = re.compile(r"(0\d{1,2}-\d{3,4}-\d{4}|1[5-9]\d{2}-\d{4})")

# 번호 바로 앞에 "팩스"/"FAX" 라벨이 붙어있으면 그 번호는 건너뛴다.
# ("팩스: 02-555-1235" 처럼 라벨과 번호 사이의 콜론·공백까지 허용)
_FAX_LABEL_RE = re.compile(r"(팩스|fax)\s*[:：]?\s*$", re.IGNORECASE)


def _first_non_fax_phone(text: str) -> str:
    """텍스트에서 팩스번호가 아닌 첫 전화번호를 찾는다.

    "대표 연락처: 02-555-1234 (팩스: 02-555-1235)" 처럼 한 줄에 전화번호와
    팩스번호가 같이 있는 경우가 흔하다. 그냥 첫 매치를 쓰면 순서에 따라
    팩스번호를 관리사무소 전화번호로 잘못 노출할 수 있어서, 번호 바로
    앞 12자 안에 "팩스"/"FAX" 라벨이 있으면 건너뛰고 다음 매치를 본다.
    (팩스번호만 있고 진짜 전화번호가 없으면 빈 문자열을 돌려준다 —
    팩스번호를 대표 연락처로 잘못 안내하는 것보다는 안전하다.)
    """
    for match in _PHONE_RE.finditer(text):
        prefix = text[max(0, match.start() - 12):match.start()]
        if _FAX_LABEL_RE.search(prefix):
            continue
        return match.group(1)
    return ""


def _find_phone_in_apartment_rules(apartment_id: int) -> str:
    """Apartment.office_phone 이 비어 있을 때의 대체 수단.

    관리사무소 연락처는 관리자가 office_phone 필드를 따로 채워 넣지
    않았어도, 이미 올려둔 단지 규정 문서(ApartmentRule → Document,
    apartments/services.py:sync_rule_to_document 가 만든다) 본문 안에
    "문의: 02-1234-5678" 같은 형태로 이미 적혀 있는 경우가 흔하다.
    그 본문을 훑어 전화번호로 보이는 첫 패턴을 찾아 대신 쓴다
    (팩스번호는 _first_non_fax_phone 이 걸러낸다).
    """
    from .models import Document, SourceType

    texts = Document.objects.filter(
        apartment_id=apartment_id,
        source_type=SourceType.APARTMENT,
        status=Document.Status.APPROVED,
    ).values_list("content_text", flat=True)

    for content_text in texts:
        phone = _first_non_fax_phone(content_text or "")
        if phone:
            return phone
    return ""


# 근거를 못 찾았을 때 카드 없이 보여줄 최소 안내 문구.
# "관련 정보를 찾을 수 없습니다" 류의 막연한 문구는 절대 쓰지 않는다.
FALLBACK_NOTICE = "문의하신 내용과 관련한 자료를 확인하지 못했습니다. 지자체 또는 아파트 관리사무소에 문의해주세요."

# 카드(관리사무소·지자체)가 하나라도 있을 때 쓰는 안내 문구.
# "정확한 안내는 관리사무소로 문의해 주세요"처럼 특정 대상을 콕 집으면
# 지자체 카드만 뜨거나 둘 다 뜬 경우 어색하다 — 카드 자체가 이미 구체적인
# 연락처를 보여주므로 문구는 일반적으로 남기고 "아래 연락처"로 안내한다.
CARDS_NOTICE = "문의하신 내용과 관련한 자료를 확인하지 못했습니다. 아래 연락처를 확인해 주세요."


def _notice_text(cards: list[dict]) -> str:
    """카드 유무에 따라 안내 문구를 고른다."""
    return CARDS_NOTICE if cards else FALLBACK_NOTICE


def _office_card(apartment) -> dict | None:
    """관리사무소 연락처 카드. 전화·주소·운영시간이 하나도 없으면 None.

    office_phone 필드가 비어 있으면 단지 규정 문서 본문에서 찾아본다
    (_find_phone_in_apartment_rules) — 관리자가 별도 필드를 안 채워도
    이미 올려둔 규정 문서에 적혀 있는 경우가 흔하다.
    """
    if not apartment:
        return None

    phone = apartment.office_phone or _find_phone_in_apartment_rules(apartment.pk)
    if not (phone or apartment.address or apartment.office_hours):
        return None

    return {
        "type": "office",
        "title": "관리사무소",
        "phone": phone,
        "address": apartment.address or "",
        "hours": apartment.office_hours or "",
    }


def _parse_kv_line(line: str) -> dict:
    """"헤더1: 값1 | 헤더2: 값2" 한 줄을 dict로 되돌린다.

    _read_csv() 가 CSV 한 행을 이 형식으로 바꿔 content_text 에 저장해
    두므로, 그 짝을 이루는 파서다.
    """
    result = {}
    for segment in line.split(" | "):
        key, sep, value = segment.partition(": ")
        if sep:
            result[key.strip()] = value.strip()
    return result


def _extract_district(address: str) -> str:
    """주소 문자열에서 "OO구"/"OO군"/"OO시" 같은 기초자치단체 단위를
    뽑는다. "서울특별시"/"OO광역시" 같은 광역 단위 접미사는 제외한다.

    "서울특별시 강남구 학동로 426" → "강남구"
    """
    for token in address.split():
        if token.endswith(("구", "군")) and not token.endswith(("특별시", "광역시")):
            return token
    for token in address.split():
        if token.endswith("시") and not token.endswith(("특별시", "광역시")):
            return token
    return ""


def _local_gov_card(apartment, region: str | None = None) -> dict | None:
    """지자체 연락처 카드. (4차 추가분)

    서비스 관리자가 CSV(지자체명/전화번호/주소/담당부서 등)를 국가·
    가이드 범위로 올려두면(rag/forms.py, DocumentUploadView) _read_csv()
    가 행마다 "헤더: 값" 한 줄로 바꿔 content_text 에 저장한다. 그 줄들
    중 이 단지 주소의 구/군/시 이름이 들어간 줄을 찾아 연락처를 뽑는다.

    아파트 미설정 사용자는 region 코드의 한글 라벨로 매칭한다.

    CSV 헤더 이름이 정확히 뭐든("지자체명"/"기관명" 등) 최대한 유연하게
    대응한다 — "전화번호" 류 키가 없으면 그 줄 안에서 전화번호 패턴을
    직접 찾는다(_PHONE_RE, 단지 규정 전화번호 추출과 동일한 방식).
    """
    # 아파트 주소에서 구/군/시 추출 시도
    district = ""
    if apartment and apartment.address:
        district = _extract_district(apartment.address)

    # 아파트가 없거나 주소에서 district를 못 뽑았으면 region 라벨로 대체
    if not district and region:
        from members.models import REGION_CHOICES
        region_label = dict(REGION_CHOICES).get(region, "")
        # "부산 남구" → "남구", "인천 미추홀구" → "미추홀구" 등 마지막 토큰 사용
        # 단일 토큰("서울", "대구" 등)은 그대로
        district = region_label.split()[-1] if region_label else ""

    if not district:
        return None

    from .models import Document, SourceType

    texts = Document.objects.filter(
        source_type=SourceType.GUIDE, status=Document.Status.APPROVED,
    ).values_list("content_text", flat=True)

    for content_text in texts:
        for line in (content_text or "").splitlines():
            if district not in line:
                continue
            row = _parse_kv_line(line)
            phone = row.get("전화번호") or row.get("연락처") or row.get("전화")
            if not phone:
                phone = _first_non_fax_phone(line)
            if not phone:
                continue
            name = row.get("지자체명") or row.get("기관명") or row.get("담당기관") or district
            return {
                "type": "local_gov",
                "title": name,
                "phone": phone,
                "address": row.get("주소", ""),
                "department": row.get("담당부서", ""),
            }
    return None


def _build_contact_cards(apartment_id: int | None, region: str | None = None) -> list[dict]:
    """근거를 못 찾았을 때 카드로 보여줄 연락처 목록. (4차 추가분)

    관리사무소 카드와 지자체 카드를 각각 시도해서 실제로 값이 있는
    것만 담는다. 아파트 미설정 사용자는 region 기반으로 지자체 카드를
    찾는다. 아무것도 못 찾으면 빈 리스트 — 호출부(ask())가
    FALLBACK_NOTICE 텍스트만으로 대체한다.
    """
    apartment = None
    if apartment_id:
        from apartments.models import Apartment

        apartment = Apartment.objects.filter(pk=apartment_id).first()

    cards = []
    office = _office_card(apartment)
    if office:
        cards.append(office)
    local_gov = _local_gov_card(apartment, region=region)
    if local_gov:
        cards.append(local_gov)
    return cards

# LangChain FAISS 벡터스토어 저장 경로 (legacy 의 index.faiss 와 별개 파일)
_LANGCHAIN_INDEX_DIR = "langchain"


def _effective_min_score(min_score: float | None) -> float:
    """유사도 임계값을 결정한다.

    파이프라인마다 score 스케일이 다르다는 문제가 생겨
    판정 로직은 rag/scoring.py 로 옮겼다.
    (RRF 는 순위 기반이라 이 임계값으로 판정하면 안 된다 — scoring.py 참고)
    """
    return scoring.effective_min_score(min_score)


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


def _retrieve(query: str, fetch_k: int, region: str | None = None) -> list[dict]:
    """질문에 대한 후보 청크를 가져온다. 여기가 유일한 분기점이다.

    반환 형식은 네 경로가 동일하다.
        [{"content", "title", "document_id", "owner_id",
          "source_type", "region", "score"}, ...]

    region 은 bm25/hybrid 에서만 쓴다. 후보 단계에서 다른 지역
    문서를 빼두면 BM25 점수 경쟁이 줄어 지역 문서가 살아남는다.
    (legacy/langchain 은 search() 의 사후 필터에 그대로 의존한다)
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

    if pipeline == "bm25":
        return bm25_store.search(query, fetch_k, region=region)

    if pipeline == "hybrid":
        query_vector = embeddings.embed_query(query)
        dense = vector_store.search(query_vector, fetch_k)
        sparse = bm25_store.search(query, fetch_k, region=region)
        return _rrf_fuse(dense, sparse, fetch_k)

    if pipeline != "legacy":
        raise ValueError(
            f"RAG_PIPELINE 값이 잘못되었습니다: {pipeline!r} "
            "(legacy, langchain, bm25, hybrid)"
        )

    query_vector = embeddings.embed_query(query)
    return vector_store.search(query_vector, fetch_k)


def _rrf_fuse(dense: list[dict], sparse: list[dict], top_k: int) -> list[dict]:
    """RRF(Reciprocal Rank Fusion)로 두 랭킹을 합친다.

        score(d) = Σ 1 / (K + rank_i(d))

    점수를 직접 더하지 않고 **순위만** 쓰는 이유:
    코사인 유사도(0~1)와 BM25 raw(0~20+)는 스케일이 달라 정규화 없이는
    더할 수 없고, 정규화 방식에 따라 결과가 흔들린다. 순위는 스케일이
    없으므로 이 문제를 통째로 피한다.

    ⚠️ 그 대가로 융합 점수는 **관련도를 뜻하지 않는다.** 무관한 질문에서도
       1위 문서는 최댓값을 받는다. 그래서 임계값 판정은 여기서 보존한
       vector_score / bm25_score 로 한다 — rag/scoring.py 참고.
    """
    k = scoring.rrf_k()
    merged: dict[str, dict] = {}

    for results, score_field in ((dense, "vector_score"), (sparse, "bm25_score")):
        for rank, item in enumerate(results, 1):
            key = f"{item.get('document_id')}::{item.get('chunk_index')}"
            entry = merged.get(key)
            if entry is None:
                entry = dict(item)
                entry["rrf_score"] = 0.0
                merged[key] = entry
            entry[score_field] = float(item.get("score", 0.0))
            entry["rrf_score"] += 1.0 / (k + rank)

    fused = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
    for item in fused:
        item["rrf_score"] = round(item["rrf_score"], 6)
        item["score"] = item["rrf_score"]
    return fused[:top_k]


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

    # BM25 는 같은 chunks 로 만든다. 순서가 어긋나면 bm25_store.load() 가
    # corpus_size 불일치로 막는다. 임베딩 API 를 쓰지 않으므로 비용 0.
    bm25_store.rebuild(chunks)

    return {
        "documents": len(documents),
        "indexed_chunks": count,
        "source": settings.RAG_SOURCE,
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "pipeline": settings.RAG_PIPELINE.lower(),
        "bm25_indexed": count,
    }


def search(
    query: str,
    top_k: int | None = None,
    owner_id: int | None = None,
    min_score: float | None = None,
    region: str | None = None,
    balanced: bool = False,
    apartment_id: int | None = None,
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
    results = _retrieve(query, fetch_k, region=region)

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

    # 4차 2R 추가분: 단지 규정 격리(fail-closed). region 필터는 None을
    # 관용적으로 통과시키지만(공통 문서 보존), apartment는 그러면 안 된다 —
    # 옛 인덱스에 남은 단지 규정이 전 사용자에게 새는 사고로 이어진다.
    # apartment_id가 None(단지 미가입 사용자)이면 apartment 청크를 전부 제거한다.
    results = [
        r for r in results
        if r.get("source_type") != "apartment" or r.get("apartment_id") == apartment_id
    ]

    # 유사도 임계값 (환각 방지 1차 장치)
    #
    # ⚠️ 파이프라인마다 score 스케일이 다르다. 예전엔 이 한 줄이
    #    r["score"] >= min_score 였는데, hybrid 의 score 는 RRF(순위
    #    역수의 합)라 이론상 최댓값이 2/(RRF_K+1) = 0.0328 이다.
    #    RAG_MIN_SCORE(0.36)와 비교하면 전부 탈락해 모든 질문이
    #    "관련 정보를 찾을 수 없습니다" 로 나간다. 반대로 bm25 의 raw
    #    score 는 상한이 없어 0.36 으로는 아무것도 못 걸러 환각 방지가
    #    무력화된다. 둘 다 에러 없이 조용히 일어난다.
    results = scoring.filter_by_threshold(results, min_score, settings.RAG_PIPELINE)

    # 재색인이 백그라운드로 바뀌면서 필요해진 장치.
    # 문서를 지워도 다음 재색인 전까지는 청크가 인덱스에 남는다.
    # 임계값 통과 뒤(=결과가 가장 적을 때) 거른다.
    results = _drop_missing_documents(results)

    # 4차 추가분: 법령 문서에 시행일 정보를 붙인다 (점수/정렬에는 영향 없음).
    results = _annotate_law_status(results)
    # 4차 2R 추가분: 단지 규정에 출처 등급·확인수·등록시점을 붙인다.
    results = _annotate_apartment_meta(results)

    if balanced:
        return _apply_quota(results, region)

    return results[:top_k]


def _retrieval_error_message(exc: Exception) -> str:
    """검색 실패 원인을 사용자에게 보여줄 한 문장으로 바꾼다.

    분류 기준은 rag/llm.py 의 _generate_openai() 와 같게 맞춘다 — 같은
    API 의 같은 오류를 두 곳이 다르게 부르면 로그를 읽을 때 헷갈린다.

    **원인 자체는 사용자에게 알리지 않는다.** "API 키가 잘못됐습니다" 같은
    문구는 사용자가 할 수 있는 일이 없는데 서비스 내부 사정만 노출한다.
    진짜 원인은 로그로 간다(journalctl -u ecobot).
    """
    msg = str(exc).lower()

    if "insufficient_quota" in msg or "quota" in msg or "429" in msg or "rate limit" in msg:
        # 지출 한도 소진이 여기로 온다. 관리자가 한도를 올리면 풀리므로
        # "잠시 후"가 맞는 안내다.
        return "현재 API 사용량이 초과되었습니다. 잠시 후 다시 질문해 주세요."

    if "api key" in msg or "401" in msg or "authentication" in msg or "invalid_api_key" in msg:
        # 키 문제는 저절로 풀리지 않는다. 기다리라고 하면 안 된다.
        return "검색 기능에 문제가 있어 답변할 수 없습니다. 관리자에게 문의해 주세요."

    return "일시적인 오류로 답변할 수 없습니다. 잠시 후 다시 시도해 주세요."


def _drop_missing_documents(results: list[dict]) -> list[dict]:
    """DB 에 없거나 승인 상태가 아닌 문서의 청크를 결과에서 제거한다.

    **왜 필요한가**

    문서 삭제가 예전에는 그 요청 안에서 rebuild_index() 까지 끝냈다. 지금은
    재색인이 백그라운드라, 삭제 직후부터 재색인이 끝나기 전까지는 지운
    문서의 청크가 인덱스에 그대로 남아 있다. 그 사이의 질문이 삭제된 문서를
    근거로 인용하면, 3차 트러블슈팅 4번("삭제한 파일의 옛 레코드가 잘못
    인용됨")이 그대로 재현된다.

    승인 취소(status != APPROVED)도 같이 막는다. 색인 대상이 애초에
    APPROVED 뿐이므로(_iter_documents), 기준을 맞춰 두는 편이 일관적이다.

    document_id 가 없는 청크(RAG_SOURCE=files 로 만든 인덱스)는 대조할
    대상이 없으므로 그대로 통과시킨다 — 여기서 막으면 파일 기반 인덱스가
    통째로 비어 버린다.
    """
    from .models import Document

    ids = {
        r["document_id"] for r in results
        if r.get("document_id") is not None
    }
    if not ids:
        return results

    alive = set(
        Document.objects.filter(
            pk__in=ids, status=Document.Status.APPROVED
        ).values_list("pk", flat=True)
    )
    # 전부 살아 있으면(대부분의 경우) 리스트를 새로 만들지 않는다.
    if len(alive) == len(ids):
        return results

    return [
        r for r in results
        if r.get("document_id") is None or r["document_id"] in alive
    ]


def _annotate_law_status(results: list[dict]) -> list[dict]:
    """법령 결과에 시행일 정보를 붙인다.

    chunks.json/FAISS 는 건드리지 않고 document_id 로 DB만 한 번 더
    조회한다 (재색인 없이 붙이려고 일부러 이렇게 골랐다). law 가 아닌
    항목이나 시행일 파싱에 실패한 법령은 그대로 통과한다(=판단 불가).
    """
    law_ids = {
        r["document_id"] for r in results
        if r.get("source_type") == "law" and r.get("document_id") is not None
    }
    if not law_ids:
        return results

    from django.utils import timezone

    from .models import Document

    rows = Document.objects.filter(pk__in=law_ids).values(
        "id", "law_effective_date", "law_doc_number"
    )
    by_id = {row["id"]: row for row in rows}
    today = timezone.localdate()

    for r in results:
        row = by_id.get(r.get("document_id"))
        if not row or not row["law_effective_date"]:
            continue
        r["law_effective_date"] = row["law_effective_date"].isoformat()
        r["law_doc_number"] = row["law_doc_number"]
        r["law_is_current"] = row["law_effective_date"] <= today

    return results


def _annotate_apartment_meta(results: list[dict]) -> list[dict]:
    """단지 규정 결과에 출처 등급·확인수·등록시점을 붙인다.

    _annotate_law_status() 와 같은 이유로 chunks.json/FAISS 는 건드리지
    않고 document_id 로 ApartmentRule을 한 번 더 조회한다. rag 가
    apartments 를 참조하는 방향이라 apartments 는 rag 를 몰라도 된다.
    """
    apt_doc_ids = {
        r["document_id"] for r in results
        if r.get("source_type") == "apartment" and r.get("document_id") is not None
    }
    if not apt_doc_ids:
        return results

    from apartments.models import ApartmentRule

    rows = ApartmentRule.objects.filter(document_id__in=apt_doc_ids).values(
        "document_id", "source_level", "created_at"
    )
    by_doc = {row["document_id"]: row for row in rows}

    for r in results:
        row = by_doc.get(r.get("document_id"))
        if not row:
            continue
        r["source_level"] = row["source_level"]
        r["registered_at"] = row["created_at"].date().isoformat()

    return results


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
    apartments = [r for r in results if r.get("source_type") == "apartment"]
    guides = [r for r in results if r.get("source_type") not in ("law", "apartment")]

    # 4차 2R 추가분: 단지 규정 자리는 "실제로 결과가 있을 때만" 배분한다.
    # 없으면 apartment_quota=0 이라 아래 계산이 기존 3분할과 완전히 같다 —
    # 4번째 그룹을 고정으로 넣으면 기존 지표(통과율 93.3%)가 무효가 된다는
    # 설계 문서의 지적을 그대로 지킨다.
    apartment_quota = settings.RAG_TOP_K_APARTMENT if apartments else 0

    if region:
        region_quota = settings.RAG_TOP_K_REGION
        common_quota = settings.RAG_TOP_K_COMMON

        # 선택한 지역 전용 문서와 전국 공통 문서를 나눈다
        local = [r for r in guides if r.get("region") == region]
        common = [r for r in guides if r.get("region") != region]

        picked = (
            apartments[:apartment_quota]
            + local[:region_quota]
            + common[:common_quota]
            + laws[:law_quota]
        )
        total = apartment_quota + region_quota + common_quota + law_quota
    else:
        guide_quota = settings.RAG_TOP_K_GUIDE
        picked = apartments[:apartment_quota] + guides[:guide_quota] + laws[:law_quota]
        total = apartment_quota + guide_quota + law_quota

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
    apartment_id: int | None = None,
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

    4차 추가분: 아직 시행되지 않은 법령(law_is_current=False)은 답변
    근거(그라운딩)에서 제외한다. LLM 이 미래 시행 조문을 현재 규정인
    것처럼 인용하면 안 되기 때문이다 — search() 는 관리자 진단 검색
    등에서 계속 전체를 보여줘야 하므로, 이 걸러내기는 여기 ask() 에서만
    한다. 제외된 법령은 버리지 않고 _law_notice() 로 "곧 이렇게
    바뀝니다" 안내에 쓴다. 오늘 날짜가 지나 그 법이 실제로 시행되면
    is_currently_effective() 가 바로 True 를 돌려주므로(값을 저장해두지
    않고 매번 오늘 날짜와 비교), 다음 질문부터는 자동으로 근거에
    포함된다 — 별도 배치 작업이 필요 없다.
    """
    # 검색은 임베딩 API 호출을 포함한다. 지출 한도가 소진되거나 키가
    # 만료되면 여기서 예외가 올라오는데, 그대로 두면 사용자에게 500 이
    # 나간다. 답변 생성(LLM) 쪽은 이미 폴백이 있으므로(rag/llm.py) 이쪽만
    # 비어 있던 셈이다.
    #
    # search() 자체는 그대로 예외를 올린다 — 관리자 진단 화면
    # (RagSearchView)에서는 원인이 그대로 보여야 한다.
    try:
        results = search(question, top_k, owner_id, region=region, balanced=True, apartment_id=apartment_id)
    except Exception as exc:
        logger.exception("검색 실패 — 질문=%r", question[:80])
        return {
            # 호출자가 통계·기록에서 제외할 수 있도록 표시한다.
            # 이건 "자료를 못 찾은" 것이 아니라 "찾아보지도 못한" 것이라,
            # '자료없음 대응률' 지표에 섞이면 수치가 오염된다.
            "error": "retrieval_unavailable",
            "answer": _retrieval_error_message(exc),
            "law": "",
            "tip": "",
            "source": "",
            "sources": [],
            "contexts": [],
            "suggested_questions": [],
            "law_notice": "",
        }

    grounding = [r for r in results if r.get("law_is_current") is not False]
    law_notice = _law_notice(results)

    # 근거가 없으면 LLM을 호출하지 않는다. (환각 방지)
    if not grounding:
        # "관련 정보를 찾을 수 없습니다" 같은 막연한 안내는 쓰지 않는다.
        # 법령 시행 전이라 제외된 경우가 아니면 항상 관리사무소·지자체
        # 문의로 안내하고, 등록된 연락처가 있으면 카드로 같이 보여준다 —
        # 실제로 답을 줄 수 있는 곳이 있으면 거기로 보내는 게 사용자에게
        # 더 도움이 된다.
        contact_cards = [] if law_notice else _build_contact_cards(apartment_id, region=region)
        return {
            "answer": (
                "관련 법령이 아직 시행되지 않아 현재 적용되는 근거를 찾을 수 없습니다."
                if law_notice else
                _notice_text(contact_cards)
            ),
            "tip": "",
            "source": "",
            "sources": [],
            "contexts": [],
            # 4차 추가분: 검색 실패 시 과거의 비슷한 질문을 추천한다.
            # (아직 시행 전 법령을 찾은 경우엔 엉뚱한 과거 질문보다
            #  law_notice 가 더 유용한 정보라 추천은 건너뛴다.)
            "suggested_questions": [] if law_notice else suggest_similar_questions(question),
            "law_notice": law_notice,
            # 4차 추가분: 관리사무소·지자체 연락처 카드.
            "contact_cards": contact_cards,
        }

    sections = _generate_answer(question, _build_context(grounding), history)
    answer_text = (sections.get("answer", "") or sections.get("guide", "")).strip()

    # 검색은 뭔가를 찾았지만(grounding 이 비어 있지 않았지만) 질문과
    # 실제로는 무관한 내용이라, LLM 이 프롬프트 지시대로 스스로 답을
    # 거부한 경우다(ANSWER_PROMPT 규칙 2, 7 — 예: 전기차 충전소 질문에
    # 에너지 절약 가이드만 검색된 경우). 이때도 "관련 정보를 찾을 수
    # 없습니다" 를 그대로 노출하지 않고 관리사무소 문의로 안내한다.
    # 답과 무관한 sources 를 같이 보여주면 오해를 주므로 함께 비운다.
    if answer_text.startswith(NO_ANSWER):
        refusal_cards = _build_contact_cards(apartment_id, region=region)
        return {
            "answer": _notice_text(refusal_cards),
            "law": "",
            "tip": "",
            "source": "",
            "sources": [],
            "contexts": [r["content"] for r in grounding],
            "law_notice": law_notice,
            "suggested_questions": suggest_similar_questions(question),
            "contact_cards": refusal_cards,
        }

    source_list = _build_sources(grounding)

    return {
        "answer": answer_text,
        "law": sections.get("law", ""),
        "tip": sections.get("tip", ""),
        "source": ", ".join(dict.fromkeys(s["title"] for s in source_list)),
        "sources": source_list,
        # RAGAS 평가용 원문 청크.
        # chat 뷰의 응답 조립이 걸러내므로 프론트 응답에는 포함되지 않는다.
        "contexts": [r["content"] for r in grounding],
        # 4차 추가분: 근거 중 아직 시행 전인 법령이 있으면 안내 문구.
        "law_notice": law_notice,
        "suggested_questions": [],
        "contact_cards": [],
    }


def suggest_similar_questions(question: str, limit: int | None = None) -> list[dict]:
    """검색 실패한 질문과 비슷하면서, 실제로 답을 찾은 적 있는 과거
    질문을 추천한다.

    chat/services.py 의 assign_cluster() 와 같은 임베딩→L2정규화→
    코사인유사도 방식을 쓰되, "임계값 이상 중 최고 1개에 편입"이 아니라
    "QUESTION_SUGGEST_THRESHOLD(병합 임계값보다 낮음) 이상을 유사도
    순으로 최대 limit개" 를 돌려준다. assign_cluster() 자체는 이미
    검증된 동작이라 손대지 않고 같은 계산을 여기 새로 둔다.

    실사용 중 확인된 문제 2건을 여기서 걸러낸다.
        1) 성공 이력이 없는 클러스터는 후보에서 제외한다. 안 그러면
           "방금 실패한 질문과 비슷한, 역시 계속 실패해온 질문"을
           추천하게 되어 도움이 안 된다 (QuestionCluster 자체에는
           성패 정보가 없어 chat.ChatLog.has_answer 를 역참조로 조회한다
           — rag 가 chat 스키마를 알아야 하는 결합이 생기지만, "성공한
           적 있는 질문만" 이라는 조건을 만족하려면 불가피하다).
        2) 지금 질문과 사실상 같은 문장(유사도가 매우 높은 클러스터)은
           제외한다. 같은 질문을 반복하면 assign_cluster() 가 기존
           클러스터에 병합하므로, 그 클러스터가 방금 실패한 "이 질문
           자체"를 스스로 추천하는 순환이 생길 수 있다 — 실제로
           재현된 문제다.
    """
    import json

    import numpy as np

    from .models import QuestionCluster

    limit = limit or settings.QUESTION_SUGGEST_LIMIT

    try:
        vec = embeddings.embed_documents([question])[0]
    except Exception:
        return []

    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm

    candidates = QuestionCluster.objects.filter(logs__has_answer=True).distinct()

    scored = []
    for cluster in candidates:
        c_vec = np.asarray(json.loads(cluster.embedding), dtype=np.float32)
        sim = float(np.dot(arr, c_vec))
        if sim >= settings.QUESTION_SUGGEST_DEDUP_THRESHOLD:
            continue  # 사실상 같은 질문 — 추천할 필요 없음
        if sim >= settings.QUESTION_SUGGEST_THRESHOLD:
            scored.append((sim, cluster))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"question": c.representative, "count": c.count}
        for _, c in scored[:limit]
    ]


def _law_notice(results: list[dict]) -> str:
    """근거 중 아직 시행 전인 법령이 있으면 안내 문구를 만든다.

    _annotate_law_status() 가 search() 안에서 이미 law_is_current 를
    붙여 놓은 상태라고 가정한다.
    """
    upcoming = [
        r for r in results
        if r.get("source_type") == "law" and r.get("law_is_current") is False
    ]
    if not upcoming:
        return ""

    labels = dict.fromkeys(
        f"{_clean_title(r.get('title', ''))}({r.get('law_effective_date')} 시행 예정)"
        for r in upcoming
    )
    return "⚠ 다음 법령은 아직 시행 전입니다 — 현재는 개정 전 조문이 적용됩니다: " + ", ".join(labels)


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
    apartments: list[str] = []
    guides: list[str] = []
    laws: list[str] = []

    for item in results:
        block = f"[{item.get('title', '제목 없음')}]\n{item['content']}"
        source_type = item.get("source_type")
        if source_type == "apartment":
            apartments.append(block)
        elif source_type == "law":
            laws.append(block)
        else:
            guides.append(block)

    # 4차 2R 추가분: 단지 규정을 맨 앞에 둔다. 단지 규정이 지자체 안내와
    # 어긋나는 경우가 흔하므로(예: 구청은 매일 배출, 단지는 수요일만),
    # llm.py 프롬프트 규칙과 짝을 맞춰 "우리 단지 규정 → 지자체·법령"
    # 우선순위를 컨텍스트 배치 순서로도 드러낸다.
    parts: list[str] = []
    if apartments:
        parts.append("### 우리 단지 규정\n" + "\n\n".join(apartments))
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

        source = {
            "document_id": item.get("document_id"),
            "title": _clean_title(item.get("title", "제목 없음")),
            "snippet": snippet,
        }
        # 4차 2R 추가분: 관리사무소 규약과 이웃 제보가 같은 형태로 나오면
        # 신뢰도를 구분할 수 없다는 설계 문서의 지적 — 등급·확인수·등록
        # 시점을 함께 노출한다. _annotate_apartment_meta() 가 이미 붙여둔
        # 값이 있을 때만 채운다 (법령/가이드 문서는 키 자체가 없다).
        if item.get("source_type") == "apartment":
            source["source_level"] = item.get("source_level")
            source["registered_at"] = item.get("registered_at")
        sources.append(source)

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

    그래서 사용자 업로드(manual)와 단지 규정(apartment)은
    **어느 모드에서든 DB 에서** 읽습니다.
        db    모드: DB 전체 (law + guide + manual + apartment)
        files 모드: 폴더 (law + guide) + DB (manual + apartment)
    """
    if settings.RAG_SOURCE.lower() == "files":
        return _load_from_files() + _load_from_db(only_db_native=True)
    return _load_from_db()


def _load_from_db(only_db_native: bool = False) -> list[dict]:
    """documents 테이블에서 문서를 읽는다.

    only_db_native=True 면 폴더에서 읽을 수 없는 문서 유형
    (manual + apartment)만 읽는다 — files 모드가 폴더의 공용 문서에
    DB 전용 문서를 합칠 때 쓴다 (_load_documents).

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

    # 4차 2R 추가분: 색인 게이트. 검토를 안 거친 데이터(단지 규정 draft 등)는
    # 여기서 걸러야 search()/_apply_quota() 로직에 영향을 안 준다.
    queryset = Document.objects.filter(status=Document.Status.APPROVED)
    if only_db_native:
        queryset = queryset.filter(source_type__in=[SourceType.MANUAL, SourceType.APARTMENT])

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
                # 4차 2R 추가분: chunking.py 가 이 값을 청크 메타에 그대로
                # 옮긴다 — search() 의 단지 격리 필터가 이 값을 본다.
                "apartment_id": row.apartment_id,
            }
        )

    if not documents and not only_db_native:
        # only_db_native(files 모드의 DB 전용 문서 읽기)에서는 0건이 정상이므로
        # 경고하지 않는다 — "seed_docs 를 실행하라"는 안내가 오해를 부른다.
        import logging

        logging.getLogger(__name__).warning(
            "documents 테이블에 인덱싱할 문서가 없습니다. "
            "python manage.py seed_docs 를 먼저 실행하십시오."
        )

    return documents


def _extract_region(filename: str) -> str:
    """파일명에서 지역 코드를 추출한다. (RAG_SOURCE=files 전용)

    키워드 매핑은 members.models.REGION_FILENAME_KEYWORDS 하나로
    통일했습니다 (예전엔 seed_docs.py 와 각자 다른 내용으로 중복돼
    있었습니다 — 지역을 늘릴 때 한쪽만 고치면 그 지역이 조용히
    "전국 공통"으로 잡히는 사고가 날 수 있었습니다).
    """
    from members.models import REGION_FILENAME_KEYWORDS

    for keyword, code in REGION_FILENAME_KEYWORDS.items():
        if keyword in filename:
            return code
    return "common"


def _load_from_files() -> list[dict]:
    """data/guide + data/docs + data/laws 폴더에서 문서를 읽는다.
    (RAG_SOURCE=files, DB 없이 테스트용)

    settings.GUIDE_DIR / DOCS_DIR / LAWS_DIR 를 참조한다.
    """
    # 4차 추가분: .csv (DocumentUploadView 와 동일하게 지원).
    supported = {".txt", ".md", ".pdf", ".csv"}
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

    if path.suffix.lower() == ".csv":
        return _read_csv(path)

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949")


def _read_csv(path: Path) -> str:
    """CSV 를 "헤더: 값" 형태의 평문으로 변환한다. (4차 추가분)

    쉼표로 이어진 원본을 그대로 색인하면 임베딩·LLM 프롬프트에 그 형태
    그대로 들어가 가독성이 떨어지고 검색 품질도 나빠진다. 첫 행을
    헤더로 보고 각 행을 "헤더1: 값1 | 헤더2: 값2" 한 줄짜리 문장으로
    바꾼다 — 값이 비어 있는 칸은 건너뛴다.

    엑셀에서 내보낸 한글 CSV는 흔히 cp949/euc-kr 인코딩이고 앞에 BOM 이
    붙기도 해서, 텍스트 파일과 같은 인코딩 폴백(utf-8 → cp949)에 BOM
    제거(utf-8-sig)까지 같이 처리한다.
    """
    import csv
    import io as _io

    raw_bytes = path.read_bytes()
    raw_text = None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            raw_text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if raw_text is None:
        return ""

    rows = [row for row in csv.reader(_io.StringIO(raw_text)) if any(cell.strip() for cell in row)]
    if not rows:
        return ""

    header, *data_rows = rows
    lines = []
    for row in data_rows:
        pairs = [f"{h.strip()}: {v.strip()}" for h, v in zip(header, row) if v.strip()]
        if pairs:
            lines.append(" | ".join(pairs))
    return "\n".join(lines)


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
