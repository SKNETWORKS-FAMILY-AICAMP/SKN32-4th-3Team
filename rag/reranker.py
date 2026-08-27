"""Cross-Encoder 기반 Reranker 모듈.

원본 대비 수정 사항:
1. 모델을 bge-reranker-large -> ko-reranker / bge-reranker-v2-m3로 변경 가능하게.
   bge-reranker-large는 중국어·영어 중심 학습이라 한국어 이득이 제한적이고,
   XLM-R large(약 560M)라 CPU 추론이 매우 느리다.
2. 입력 dict를 in-place로 변형하지 않는다. (원본은 호출자의 리스트를 오염시킴)
3. 원래 검색 점수를 retrieval_score로 보존한다.
   원본은 score를 raw logit으로 덮어써서, score를 임계값으로 쓰는
   상위 로직(자료 없음 판정 등)이 조용히 깨진다.
4. sigmoid 정규화된 rerank_score(0~1)를 함께 제공한다.
5. max_length / batch_size 명시. 미지정 시 긴 한국어 청크가 잘린다.
6. 후보 수 상한(MAX_CANDIDATES). 100개를 그대로 넣으면 지연시간이 초 단위가 된다.
7. 모델 로딩에 lock을 걸고, 실패 시 원래 순서를 그대로 돌려준다(폴백).
"""
from __future__ import annotations

import logging
import math
import threading

logger = logging.getLogger(__name__)

# 후보 선택 기준
#   Dongjin-kr/ko-reranker      : bge-reranker-large의 한국어 파인튜닝. 한국어 우선이면 이것.
#   BAAI/bge-reranker-v2-m3     : 다국어(한국어 포함) 최신 계열. 범용성 우선이면 이것.
#   BAAI/bge-reranker-base      : 가볍고 빠름. CPU 배포면 이것.
_DEFAULT_MODEL = "Dongjin-kr/ko-reranker"

MAX_CANDIDATES = 30   # 리랭킹에 넣을 1차 후보 상한
MAX_LENGTH = 512      # 토큰 기준. 청크가 길면 늘리되 지연시간과 trade-off
BATCH_SIZE = 16

_model = None
_model_lock = threading.Lock()
_load_failed = False


def _model_name() -> str:
    try:
        from django.conf import settings
        return getattr(settings, "RERANKER_MODEL", _DEFAULT_MODEL)
    except Exception:
        return _DEFAULT_MODEL


def get_reranker():
    """CrossEncoder를 지연 로딩한다. 실패하면 None을 반환한다."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None

    with _model_lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(_model_name(), max_length=MAX_LENGTH)
            logger.info("reranker loaded: %s", _model_name())
        except Exception as exc:
            _load_failed = True
            logger.warning("reranker 로딩 실패, 1차 검색 순서를 그대로 사용합니다: %r", exc)
            return None
    return _model


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def rerank(
    query: str,
    chunks: list[dict],
    top_k: int = 5,
    max_candidates: int = MAX_CANDIDATES,
) -> list[dict]:
    """1차 검색 후보를 Cross-Encoder로 재정렬한다.

    원본 chunks는 변경하지 않고 복사본을 반환한다.
    모델을 못 쓰는 상황이면 1차 검색 순서 상위 top_k를 그대로 돌려준다.
    """
    if not chunks:
        return []

    candidates = [dict(c) for c in chunks[:max_candidates]]

    model = get_reranker()
    if model is None:
        for rank, item in enumerate(candidates[:top_k], 1):
            item["rank"] = rank
            item["reranked"] = False
        return candidates[:top_k]

    pairs = [[query, c.get("content", "")] for c in candidates]
    try:
        scores = model.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False)
    except Exception as exc:
        logger.warning("rerank 추론 실패, 1차 순서 유지: %r", exc)
        for rank, item in enumerate(candidates[:top_k], 1):
            item["rank"] = rank
            item["reranked"] = False
        return candidates[:top_k]

    for item, raw in zip(candidates, scores):
        raw = float(raw)
        # 원래 검색 점수는 보존한다. score를 덮어쓰면 임계값 로직이 깨진다.
        item["retrieval_score"] = item.get("score")
        item["rerank_logit"] = round(raw, 4)
        item["rerank_score"] = round(_sigmoid(raw), 4)  # 0~1 정규화

    candidates.sort(key=lambda x: x["rerank_logit"], reverse=True)

    top = candidates[:top_k]
    for rank, item in enumerate(top, 1):
        item["rank"] = rank
        item["reranked"] = True
        item["score"] = item["rerank_score"]   # 정규화된 값으로만 덮어쓴다
    return top
