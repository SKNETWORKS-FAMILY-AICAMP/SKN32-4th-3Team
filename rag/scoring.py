# path : rag/scoring.py
"""
[RAG 파트] 파이프라인별 관련도 임계값 판정.

■ 이 파일이 왜 생겼나

`search()` 의 환각 방지 1차 장치는 원래 한 줄이었습니다.

    results = [r for r in results if r.get("score", 0.0) >= min_score]

파이프라인이 legacy 하나일 때는 이걸로 충분했습니다. score 가 항상
코사인 유사도(0~1)였으니까요. BM25 와 hybrid 가 들어오면서 **같은
score 필드에 서로 다른 스케일의 숫자**가 담기게 됐습니다.

    legacy  코사인 유사도        0.0 ~ 1.0     (openai 실사용 0.2~0.6)
    bm25    BM25 raw score      0.0 ~ 상한없음 (실사용 3~20)
    hybrid  RRF (순위 역수 합)   0.0 ~ 2/(K+1) = 0.0328  (K=60)

RAG_MIN_SCORE(=0.36) 하나로 셋을 다 거르면 이렇게 됩니다.

    hybrid -> 이론상 최댓값 0.0328 < 0.36. **전부 탈락.**
              ask() 는 근거 0건이면 LLM 을 호출하지 않으므로
              모든 질문에 "관련 정보를 찾을 수 없습니다" 가 나갑니다.
    bm25   -> 대부분 3 이상이라 아무것도 못 거릅니다.
              환각 방지 1차 장치가 통째로 무력화됩니다.

두 경우 모두 **에러 없이 조용히** 일어납니다. 3차 보고서 6장이
"측정 도구가 발견한 결함"으로 모은 것과 같은 종류입니다.

■ RRF 를 0~1 로 정규화하면 되지 않나 — 안 됩니다

RRF 는 순위 기반입니다. "오늘 날씨 어때?" 처럼 완전히 무관한
질문이어도 두 랭킹의 1위 문서는 반드시 존재하고, 그 문서의 RRF 는
관련도와 무관하게 최댓값 근처를 받습니다. 정규화하면 그 값이 1.0 이
되어 어떤 임계값도 통과합니다. **장애가 환각으로 바뀔 뿐입니다.**

그래서 hybrid 는 융합 점수가 아니라 **융합 전 원점수**로 판정합니다.
_rrf_fuse() 가 vector_score / bm25_score 를 보존해 둡니다.
"""
from __future__ import annotations

from django.conf import settings

# RRF 이론상 최댓값 = 두 랭킹 모두 1위일 때 1/(K+1) + 1/(K+1)
RRF_SOURCES = 2


def rrf_k() -> int:
    return int(getattr(settings, "RAG_RRF_K", 60))


def rrf_ceiling() -> float:
    """RRF 점수의 이론상 상한. **표시용 정규화에만** 쓴다."""
    return RRF_SOURCES / (rrf_k() + 1)


def bm25_min_score() -> float:
    """BM25 스케일 임계값.

    기본 0.0(무필터). 실제 값은 `manage.py measure_threshold` 로
    측정해서 정할 것 — no_answer 질문의 최고 BM25 점수와 answerable
    질문의 분포가 갈리는 지점이 임계값이다.
    """
    return float(getattr(settings, "RAG_MIN_SCORE_BM25", 0.0))


def effective_min_score(min_score: float | None = None) -> float:
    """벡터 유사도 스케일의 임계값.

    기존 service._effective_min_score() 본문을 그대로 옮긴 것이다.
    백엔드마다 유사도 분포가 다르므로 백엔드를 바꾸면
    manage.py measure_threshold 로 재측정해야 한다.
      hash   : 표면 문자열 일치만 잡아 0.05~0.15 -> 전용 임계값
      local  : sentence-transformers. 의미 기반이라 점수대가 높다
      gemini : 0.3~0.7
      openai : 0.2~0.6
    """
    if min_score is not None:
        return min_score
    if settings.EMBEDDING_BACKEND.lower() == "hash":
        return settings.RAG_MIN_SCORE_LOCAL
    return settings.RAG_MIN_SCORE


def passes_threshold(item: dict, min_score: float, pipeline: str | None = None) -> bool:
    """이 청크가 근거로 삼기에 충분히 관련 있는가.

    판정 기준은 '순위'가 아니라 '의미 유사도'다.

    hybrid : 벡터 후보에 든 청크는 vector_score 로 판정.
             BM25 단독 히트(벡터 top-k 밖)는 BM25 스케일로 판정.
    bm25   : BM25 스케일
    그 외   : score (코사인 유사도)
    """
    pipeline = (pipeline or settings.RAG_PIPELINE).lower()

    if pipeline == "hybrid":
        if "vector_score" in item:
            return float(item["vector_score"]) >= min_score
        return float(item.get("bm25_score", 0.0)) >= bm25_min_score()

    if pipeline == "bm25":
        return float(item.get("bm25_score", item.get("score", 0.0))) >= bm25_min_score()

    return float(item.get("score", 0.0)) >= min_score


def filter_by_threshold(
    results: list[dict],
    min_score: float,
    pipeline: str | None = None,
) -> list[dict]:
    """search() 에서 쓰는 진입점."""
    pipeline = (pipeline or settings.RAG_PIPELINE).lower()
    return [r for r in results if passes_threshold(r, min_score, pipeline)]


def annotate_normalized_score(results: list[dict]) -> list[dict]:
    """hybrid 결과에 0~1 로 편 표시용 점수를 붙인다.

    score 필드는 건드리지 않는다 — _apply_quota() 의 정렬 기준이라
    바꾸면 자리배분 순서가 흔들린다. 화면에 신뢰도를 보여줄 때만 쓸 것.
    **관련도 판정에는 절대 쓰지 말 것** (모듈 docstring 참고).
    """
    ceiling = rrf_ceiling()
    for item in results:
        if "rrf_score" in item and ceiling > 0:
            item["score_display"] = round(min(item["rrf_score"] / ceiling, 1.0), 4)
    return results
