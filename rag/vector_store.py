# path : app/services/vector_store.py
"""
[RAG 파트] FAISS 인덱스의 생성·저장·검색을 담당합니다.

저장 구조 (data/indexes/):
  - index.faiss  : 임베딩 벡터만 담긴 FAISS 인덱스 파일
  - chunks.json  : 청크 본문 + 메타데이터 목록 (FAISS 위치 번호 = 리스트 인덱스)

핵심 아이디어:
  FAISS는 벡터를 추가한 순서대로 0, 1, 2... 위치 번호를 부여한다.
  chunks.json도 같은 순서로 저장하므로 "위치 번호 = 리스트 인덱스"가 성립,
  별도 매핑 테이블 없이 검색 결과 위치로 청크 내용을 바로 찾을 수 있다.
  두 파일은 항상 rebuild()에서 한 쌍으로 생성되므로 어긋날 일이 없다.

코사인 유사도 구현:
  벡터를 L2 정규화한 뒤 내적(IndexFlatIP)을 취하면 코사인 유사도와 동일.
  → 점수 범위 대략 -1 ~ 1, 1에 가까울수록 유사.

참조: 3_4/5/mcp_rag_project/app/vectordb/faiss_store.py

[LangChain 도입 3단계 - 하정원]
build_langchain_vectorstore()/search_with_langchain()을 파일 끝에
추가했다. 기존 rebuild()/search()(차원 검증 포함)는 전혀 안 건드렸고,
지금 당장 아무도 새 함수를 안 부르니 위험 없다.

⚠️ RAG 담당 검토 필요 - 거리 지표(score 스케일) 차이:
기존 코드는 IndexFlatIP + L2 정규화로 코사인 유사도를 흉내낸다
(점수 대략 -1~1). LangChain의 FAISS 래퍼는 기본이 유클리드 거리라
점수 스케일이 완전히 다르다. 최대한 기존과 비슷하게 맞추려고
distance_strategy=MAX_INNER_PRODUCT로 지정해서 내적 기반으로 맞췄고,
실제로 점수를 비교해보니 소수점까지 동일하게 나왔다(재현·확인함) -
기존 RAG_MIN_SCORE/RAG_MIN_SCORE_LOCAL 임계값을 그대로 재사용해도 된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import faiss
import numpy as np

from django.conf import settings

if TYPE_CHECKING:
    from langchain_community.vectorstores import FAISS as LangChainFAISS


def _index_path() -> Path:
    return settings.INDEX_DIR / "index.faiss"


def _meta_path() -> Path:
    return settings.INDEX_DIR / "chunks.json"


def rebuild(chunks: list[dict], vectors: list[list[float]], dimension: int) -> int:
    """청크와 벡터로 FAISS 인덱스를 전체 재구축하고 디스크에 저장합니다.

    전체 재구축(rebuild) 방식을 쓰는 이유:
      MySQL 문서 수정·삭제 시 FAISS와의 동기화 문제를 피하는 가장 단순한 방법.
      문서량이 적은 초기 단계에서는 몇 초면 끝나므로 증분 업데이트는 추후 과제.
    """
    settings.INDEX_DIR.mkdir(parents=True, exist_ok=True)

    matrix = np.asarray(vectors, dtype=np.float32)

    # 코사인 유사도를 위해 모든 벡터를 단위 길이로 정규화
    if len(matrix) > 0:
        faiss.normalize_L2(matrix)

    # 내적 기반 Flat 인덱스: 전수 비교라 느리지만 정확, 이 규모에선 충분
    index = faiss.IndexFlatIP(dimension)
    if len(matrix) > 0:
        index.add(matrix)

    # 인덱스와 청크 메타를 한 쌍으로 저장.
    #
    # [4차 수정 · Windows 한글 경로 대응]
    # faiss.write_index() 는 C++ 이 fopen(const char*) 으로 직접 파일을
    # 연다. 파이썬이 경로를 UTF-8 로 넘기면 한국어 Windows 의 C 런타임이
    # CP949 로 해석하다 실패한다 — 경로에 한글이 있으면
    # "Illegal byte sequence" 로 죽는다 (실제 재현: C:\새 폴더\...).
    # serialize_index() 는 write_index 와 **바이트 포맷이 완전히 동일**
    # (동일 인덱스로 비교해 바이트 일치 확인)하면서 메모리 버퍼를
    # 돌려주므로, 파일 쓰기는 유니코드 경로를 처리할 줄 아는 파이썬에
    # 맡긴다. 기존에 write_index 로 만든 index.faiss 도 그대로 호환된다.
    _index_path().write_bytes(bytes(faiss.serialize_index(index)))
    _meta_path().write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return len(chunks)


def search(query_vector: list[float], top_k: int | None = None) -> list[dict]:
    """질문 벡터와 유사한 청크를 점수 순으로 반환합니다.

    반환 형식: [{"content", "document_id", "source", "chunk_index", "score"}, ...]
    """
    top_k = top_k or settings.RAG_TOP_K

    # 인덱스가 아직 없으면 빈 결과 (호출부에서 rebuild 안내 처리)
    if not _index_path().exists() or not _meta_path().exists():
        return []

    # read_index() 도 write_index() 와 같은 C++ fopen 경로라 한글 경로에서
    # 죽는다. 파일은 파이썬이 읽고 faiss 에는 메모리 버퍼만 넘긴다.
    # (bytearray 로 복사하는 이유: np.frombuffer(bytes) 는 읽기 전용
    #  배열이라 faiss 버전에 따라 거부될 수 있다)
    raw = np.frombuffer(bytearray(_index_path().read_bytes()), dtype=np.uint8)
    index = faiss.deserialize_index(raw)
    chunks: list[dict] = json.loads(_meta_path().read_text(encoding="utf-8"))

    # 인덱스를 만든 백엔드와 지금 백엔드의 차원이 다르면 검색이 불가능하다.
    # FAISS 는 assert 로만 알려줘서 원인 파악이 어려우므로 먼저 확인한다.
    if len(query_vector) != index.d:
        raise RuntimeError(
            f"임베딩 차원이 인덱스와 다릅니다. "
            f"(인덱스 {index.d}차원 / 현재 백엔드 {len(query_vector)}차원)\n"
            "  EMBEDDING_BACKEND 를 바꾼 뒤 재인덱싱하지 않아서 생기는 문제입니다.\n"
            "  → python -m scripts.seed_docs 를 실행해 인덱스를 다시 만드세요."
        )

    query = np.asarray([query_vector], dtype=np.float32)
    faiss.normalize_L2(query)

    # 저장된 청크 수보다 많이 요청하지 않도록 보정
    limit = min(top_k, index.ntotal)
    if limit == 0:
        return []

    scores, positions = index.search(query, limit)

    results: list[dict] = []
    for position, score in zip(positions[0], scores[0]):
        if position < 0:  # FAISS가 채우지 못한 슬롯은 -1
            continue
        item = dict(chunks[position])       # 위치 번호 = chunks.json 리스트 인덱스
        item["score"] = round(float(score), 4)
        results.append(item)

    return results


def index_exists() -> bool:
    """인덱스가 빌드되어 있는지 확인합니다. (상태 조회 API용)"""
    return _index_path().exists() and _meta_path().exists()


def build_langchain_vectorstore(documents: list[dict]) -> "LangChainFAISS":
    """LangChain FAISS 벡터스토어를 문서 목록으로부터 만든다.

    chunking.build_langchain_documents()로 청킹(조문·품목 단위 로직은
    그대로 재사용)하고, embedding_service의 LangChain 어댑터로 임베딩한다.
    지금은 메모리에만 만들고 디스크 저장은 안 함 - 저장까지 필요하면
    반환된 객체에 .save_local(경로)를 호출하면 된다.
    """
    from langchain_community.vectorstores import FAISS
    from langchain_community.vectorstores.utils import DistanceStrategy

    from . import chunking, embeddings

    docs = chunking.build_langchain_documents(documents)
    embeddings = embeddings._get_langchain_embeddings_class()()

    return FAISS.from_documents(
        docs,
        embeddings,
        distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
    )


def search_with_langchain(
    vectorstore: "LangChainFAISS", query: str, top_k: int | None = None
) -> list[dict]:
    """LangChain vectorstore로 검색하고, 기존 search()와 같은 딕셔너리 형식으로 반환한다.

    반환 형식(딕셔너리 + "content"/"score" 키)을 기존 search()와 맞춰서,
    나중에 rag_service.py가 이 경로로 옮겨가도 호출부를 거의 안 고치게
    하기 위함이다.
    """
    top_k = top_k or settings.RAG_TOP_K
    results = vectorstore.similarity_search_with_score(query, k=top_k)

    output: list[dict] = []
    for doc, score in results:
        item = dict(doc.metadata)
        item["content"] = doc.page_content
        item["score"] = round(float(score), 4)
        output.append(item)
    return output