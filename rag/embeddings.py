# path : app/services/embeddings.py
"""
[RAG 파트] 텍스트를 임베딩 벡터로 변환합니다.

백엔드 3종을 .env의 EMBEDDING_BACKEND 값으로 전환:
  - "local"  : sentence-transformers 로컬 임베딩. API 키 불필요, CPU만으로 동작.
  - "gemini" : Gemini Embeddings API. (무료 등급은 일일 요청 한도가 있음)
  - "openai" : OpenAI Embeddings API. 일일 한도가 없어 대량 인덱싱에 안정적.
  - "hash"   : 해시 기반 결정적 임베딩. 파이프라인 검증용 (의미 유사도 없음)

인터페이스는 embed_documents / embed_query 두 개로 고정.
백엔드를 바꿔도 호출하는 쪽(rag_service)은 수정할 필요가 없습니다.

[LangChain 도입 2단계 - 하정원]
_get_langchain_embeddings_class()를 파일 끝에 추가했다. 기존
embed_documents/embed_query(캐시 포함)는 전혀 안 건드렸고, 그 함수들에
그대로 위임하는 LangChain Embeddings 어댑터만 얹었다. 지금 당장 아무도
이 함수를 안 부르니 위험 없음.

⚠️ 처음엔 별도 클래스를 만들어서 Embeddings를 상속 안 하고 같은 이름의
메서드만 구현해서 의존을 줄이려 했는데, 실제로 LangChain의 FAISS가
내부에서 isinstance(embedding_function, Embeddings)로 검사해서 "object
is not callable" 에러가 났다 (재현·확인함). 상속이 필수였다. 그런데
믹스인으로 다중 상속하면 Embeddings가 Pydantic 기반이라 MRO가 꼬여
"Can't instantiate abstract class" 에러가 또 났다 (역시 재현·확인함).
그래서 함수 안에서 단일 클래스로 직접 상속하는 방식으로 정리했다.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

from django.conf import settings

# ─── sentence-transformers 모델 (지연 로드, 싱글톤) ───────────────
_st_model = None
_ST_MODEL_NAME = "intfloat/multilingual-e5-small"  # 384d, 한국어 지원, CPU 적합
_ST_DIMENSION = 384


def _get_st_model():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer
        print(f"[임베딩] sentence-transformers 모델 로드 중: {_ST_MODEL_NAME}")
        _st_model = SentenceTransformer(_ST_MODEL_NAME)
        print(f"[임베딩] 모델 로드 완료 (차원: {_ST_DIMENSION})")
    return _st_model


def get_dimension() -> int:
    """현재 백엔드의 임베딩 벡터 차원을 반환합니다."""
    backend = settings.EMBEDDING_BACKEND.lower()
    if backend == "gemini":
        return settings.GEMINI_EMBEDDING_DIMENSION
    if backend == "openai":
        return settings.OPENAI_EMBEDDING_DIMENSION
    if backend == "hash":
        return settings.LOCAL_EMBEDDING_DIMENSION
    # local (sentence-transformers)
    return _ST_DIMENSION


def embed_documents(texts: list[str]) -> list[list[float]]:
    """문서(청크) 목록을 임베딩 벡터 목록으로 변환합니다."""
    if not texts:
        return []
    backend = settings.EMBEDDING_BACKEND.lower()
    if backend in ("gemini", "openai"):
        # 외부 API 는 호출 비용·할당량이 있으므로 디스크 캐시를 거친다
        return _embed_api_cached(texts, backend)
    if backend == "hash":
        return [_embed_hash(t) for t in texts]
    # local (sentence-transformers) — 로컬 계산이라 캐시 불필요
    return _embed_local_st(texts)


def embed_query(text: str) -> list[float]:
    """검색 질문 하나를 임베딩 벡터로 변환합니다."""
    return embed_documents([text])[0]


# ─────────────────────────── 내부 구현 ───────────────────────────


def _embed_local_st(texts: list[str]) -> list[list[float]]:
    """sentence-transformers 로컬 임베딩 (실서비스용).

    multilingual-e5 모델은 입력 앞에 "query: " 또는 "passage: " 접두사를 붙여야
    성능이 좋지만, 문서/질문 구분 없이 쓸 때는 생략해도 충분히 동작한다.
    """
    model = _get_st_model()
    # e5 모델 권장: 접두사 추가
    prefixed = [f"query: {t}" for t in texts]
    embeddings = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=len(texts) > 50)
    return embeddings.tolist()


def _embed_hash(text: str) -> list[float]:
    """해시 기반 임베딩 (파이프라인 검증용). 의미 유사도 없음."""
    dim = settings.LOCAL_EMBEDDING_DIMENSION
    vector = np.zeros(dim, dtype=np.float32)

    words = text.lower().split()
    tokens = list(words)
    for word in words:
        if len(word) >= 2:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        vector = vector / norm
    return vector.tolist()


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    """Gemini Embeddings API 호출."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY가 설정되지 않았습니다. "
            ".env에 키를 넣거나 EMBEDDING_BACKEND=local로 되돌리세요."
        )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    config = types.EmbedContentConfig(
        output_dimensionality=settings.GEMINI_EMBEDDING_DIMENSION,
    )

    batch_size = max(1, min(settings.GEMINI_EMBEDDING_BATCH, 100))
    total = len(texts)
    vectors: list[list[float]] = []

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]

        for attempt in range(1, 4):
            try:
                response = client.models.embed_content(
                    model=settings.GEMINI_EMBEDDING_MODEL,
                    contents=batch,
                    config=config,
                )
                vectors.extend(list(e.values) for e in response.embeddings)
                break
            except Exception as exc:
                if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                    raise
                if attempt == 3:
                    raise
                wait = 20 * attempt
                print(f"    요청 한도 초과. {wait}초 대기 후 재시도 ({attempt}/3)")
                time.sleep(wait)

        done = min(start + batch_size, total)
        print(f"    임베딩 {done}/{total}")

    return vectors


# ─────────────────── 외부 API 캐시 (RAG 파트 추가) ───────────────────
#
# 왜 캐시하나
#   rebuild 할 때마다 전체 청크를 다시 임베딩하면
#     - Gemini 무료 등급은 일일 한도가 금방 소진되고
#     - OpenAI 는 호출 비용이 반복해서 발생한다.
#   같은 텍스트는 항상 같은 벡터이므로 디스크에 저장해 재사용한다.
#   배치마다 저장하므로 중간에 429 로 끊겨도 다시 실행하면 이어서 진행된다.


def _cache_path() -> Path:
    return settings.INDEX_DIR / "embedding_cache.json"


def _cache_key(text: str, backend: str) -> str:
    """백엔드·모델·차원이 바뀌면 다른 벡터가 되므로 키에 함께 넣는다."""
    model = (
        settings.OPENAI_EMBEDDING_MODEL
        if backend == "openai"
        else settings.GEMINI_EMBEDDING_MODEL
    )
    raw = f"{backend}|{model}|{get_dimension()}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print("[임베딩] 캐시 파일을 읽지 못해 새로 만듭니다.")
        return {}


def _save_cache(cache: dict) -> None:
    settings.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path().write_text(json.dumps(cache), encoding="utf-8")


def _embed_api_cached(texts: list[str], backend: str) -> list[list[float]]:
    """캐시에 없는 텍스트만 API로 임베딩한다."""
    cache = _load_cache()

    missing: list[str] = []
    seen: set = set()
    for text in texts:
        key = _cache_key(text, backend)
        if key not in cache and key not in seen:
            seen.add(key)
            missing.append(text)

    hit = len(texts) - len(missing)
    if hit:
        print(f"    캐시 재사용 {hit}개 / 신규 {len(missing)}개")

    if missing:
        vectors = _embed_openai(missing) if backend == "openai" else _embed_gemini(missing)
        for text, vector in zip(missing, vectors):
            cache[_cache_key(text, backend)] = vector
        _save_cache(cache)

    return [cache[_cache_key(t, backend)] for t in texts]


def _embed_openai(texts: list[str]) -> list[list[float]]:
    """OpenAI Embeddings API 호출.

    Gemini 무료 등급과 달리 일일 요청 한도가 없어 대량 인덱싱에 안정적이다.

        pip install openai
    """
    if not settings.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            ".env에 키를 넣거나 EMBEDDING_BACKEND를 local·gemini 로 바꾸세요."
        )

    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    batch_size = max(1, min(settings.GEMINI_EMBEDDING_BATCH, 100))
    total = len(texts)
    vectors: list[list[float]] = []

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]
        response = client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=batch,
            dimensions=settings.OPENAI_EMBEDDING_DIMENSION,
        )
        # index 순서가 보장되지 않을 수 있어 정렬 후 사용
        vectors.extend(
            item.embedding for item in sorted(response.data, key=lambda d: d.index)
        )
        print(f"    임베딩 {min(start + batch_size, total)}/{total}")

    return vectors


# ─────────────── LangChain 어댑터 (LangChain 도입 2단계, 하정원 추가) ───────────────


def _get_langchain_embeddings_class():
    """langchain_core.embeddings.Embeddings를 상속한 어댑터 클래스를 반환한다.

    LangChain의 벡터스토어(FAISS 등)가 요구하는 embed_documents/embed_query를
    위의 기존 함수(캐시 포함)에 그대로 위임한다. 백엔드 전환
    (local/gemini/openai/hash) 로직은 하나도 새로 안 만들고 재사용한다.
    """
    from langchain_core.embeddings import Embeddings

    class _LangChainEmbeddingsImpl(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return embed_documents(texts)

        def embed_query(self, text: str) -> list[float]:
            return embed_query(text)

    return _LangChainEmbeddingsImpl