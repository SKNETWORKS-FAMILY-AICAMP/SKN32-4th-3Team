# path : rag/bm25_store.py
"""
[RAG 파트] BM25(sparse) 인덱스의 생성·저장·검색을 담당합니다.

vector_store.py 와 **같은 chunks.json 을 기준**으로 인덱스를 만듭니다.
FAISS 위치 번호 = chunks.json 리스트 인덱스라는 기존 규약을 그대로 쓰므로,
BM25 결과와 벡터 결과가 같은 청크를 가리키는 것이 구조적으로 보장됩니다.

저장 구조 (INDEX_DIR):
  - index.faiss  : 벡터 (vector_store)
  - bm25.pkl     : BM25 통계 (이 파일)
  - chunks.json  : 청크 본문 + 메타 (두 인덱스가 공유)

왜 BM25 인가
────────────
벡터 검색은 의미가 비슷하면 찾지만, **고유명사와 숫자에 약합니다.**
"월요일"과 "화요일"의 임베딩은 거의 같은 지점에 있고,
"1599-0903", "제15조", "부산 남구" 같은 토큰도 뭉개집니다.
배출 요일·전화번호·조문 번호가 핵심인 이 도메인에서는 치명적입니다.

BM25 는 정확한 단어 일치를 세므로 그 약점을 정확히 보완합니다.
단, 한국어에서는 조사 분리가 전제입니다 — rag/tokenizer.py 참고.

⚠️ 인덱스 버전
토크나이저가 바뀌면 색인과 질의가 다르게 잘려 검색이 **에러 없이**
망가집니다. payload 에 버전과 토크나이저 이름을 남기고 load() 에서
검증합니다. 옛 인덱스로 잘못된 결과가 나오는 것보다 즉시 죽는 게 낫습니다.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

from django.conf import settings

from rag.tokenizer import tokenize, tokenizer_name

INDEX_VERSION = 1

# region 이 이 값이면 전국 공통 문서로 본다.
# service.search() 의 지역 필터와 같은 규약이다.
COMMON_REGIONS = (None, "", "common")


def _index_path() -> Path:
    return settings.INDEX_DIR / "bm25.pkl"


def _meta_path() -> Path:
    return settings.INDEX_DIR / "chunks.json"


def index_exists() -> bool:
    return _index_path().exists() and _meta_path().exists()


def rebuild(chunks: list[dict]) -> int:
    """chunks 로 BM25 인덱스를 만들고 bm25.pkl 에 저장한다.

    vector_store.rebuild() 와 **같은 chunks 리스트**를 받아야 한다.
    순서가 어긋나면 load() 의 corpus_size 검증에서 걸린다.
    """
    from rank_bm25 import BM25Okapi

    settings.INDEX_DIR.mkdir(parents=True, exist_ok=True)

    corpus = [tokenize(c.get("content", "")) for c in chunks]

    # 빈 코퍼스에 BM25Okapi 를 만들면 division by zero 가 난다.
    bm25 = BM25Okapi(corpus) if corpus else None

    payload = {
        "version": INDEX_VERSION,
        "corpus_size": len(chunks),
        "tokenizer": tokenizer_name(),
        "k1": getattr(settings, "RAG_BM25_K1", 1.5),
        "b": getattr(settings, "RAG_BM25_B", 0.75),
        "bm25": bm25,
    }
    _index_path().write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
    return len(chunks)


def load():
    """저장된 BM25 와 같은 순서의 chunks.json 을 읽는다."""
    if not index_exists():
        raise FileNotFoundError(
            "BM25 인덱스가 없습니다. `python manage.py seed_docs --reindex` 를 실행하세요."
        )

    payload = pickle.loads(_index_path().read_bytes())
    chunks: list[dict] = json.loads(_meta_path().read_text(encoding="utf-8"))

    if payload.get("version") != INDEX_VERSION:
        raise RuntimeError(
            f"BM25 인덱스 버전이 {payload.get('version')} 입니다(현재 {INDEX_VERSION}). "
            "재색인하세요."
        )

    current = tokenizer_name()
    if payload.get("tokenizer") != current:
        raise RuntimeError(
            f"색인 토크나이저({payload.get('tokenizer')})와 "
            f"현재 토크나이저({current})가 다릅니다. "
            "질의와 문서가 다르게 잘리면 검색이 조용히 망가집니다. 재색인하세요."
        )

    if payload.get("corpus_size") != len(chunks):
        raise RuntimeError(
            "BM25 인덱스와 chunks.json 의 청크 수가 다릅니다. 재색인하세요."
        )

    return payload["bm25"], chunks


def region_allows(chunk_region, target_region) -> bool:
    """chunk 가 target_region 질의의 후보가 될 수 있는가.

    service.search() 의 사후 필터와 같은 규약(해당 지역 + 전국 공통).
    여기서는 후보 단계에서 미리 걸러 BM25 점수 경쟁 자체를 줄인다.
    """
    if target_region in COMMON_REGIONS:
        return True
    if chunk_region == target_region:
        return True
    return chunk_region in COMMON_REGIONS


def search(
    query: str,
    top_k: int | None = None,
    region: str | None = None,
) -> list[dict]:
    """BM25 점수 순으로 청크를 반환한다.

    반환 형식은 vector_store.search() 와 맞춘다.
        [{... 청크 메타 ..., "score": float, "bm25_score": float}, ...]

    score 에 BM25 raw 점수가 들어간다는 점에 주의할 것.
    코사인 유사도(0~1)와 스케일이 완전히 다르므로 RAG_MIN_SCORE 를
    그대로 적용하면 안 된다 — rag/scoring.py 가 이 분기를 처리한다.
    """
    top_k = top_k or settings.RAG_TOP_K
    bm25, chunks = load()
    if not chunks or bm25 is None:
        return []

    scores = bm25.get_scores(tokenize(query))

    positions = [
        i for i in range(len(chunks))
        if region_allows(chunks[i].get("region"), region)
    ]
    if not positions:
        return []

    positions.sort(key=lambda i: scores[i], reverse=True)
    positions = positions[: min(top_k, len(positions))]

    results: list[dict] = []
    for pos in positions:
        item = dict(chunks[pos])
        value = round(float(scores[pos]), 4)
        item["score"] = value
        item["bm25_score"] = value
        results.append(item)
    return results
