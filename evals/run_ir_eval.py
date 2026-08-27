from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

import django


# ============================================================
# Django
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()


from django.test import override_settings

from rag import service


# ============================================================
# 설정
# ============================================================

EVALS_DIR = BASE_DIR / "evals"

GOLD_PATH = EVALS_DIR / "qa_set_with_gold.json"

RESULT_PATH = EVALS_DIR / "ir_eval_results.json"
DETAIL_PATH = EVALS_DIR / "ir_eval_details.json"

PIPELINES = (
    "legacy",
    "bm25",
    "hybrid",
)

FETCH_K = 100

EVAL_K = (
    1,
    3,
    5,
)


# ============================================================
# Chunk ID
# ============================================================

def chunk_key(result: dict) -> str:
    """
    검색 결과를 실제 chunk 식별자로 변환.

    현재 프로젝트 chunks.json 구조:
        document_id
        chunk_index

    예:
        document_id=6
        chunk_index=16

        -> "6:16"
    """

    return (
        f"{result.get('document_id')}:"
        f"{result.get('chunk_index')}"
    )


# ============================================================
# 정답 chunk
# ============================================================

def get_gold_keys(item: dict) -> set[str]:
    return set(
        item.get("relevant_chunk_keys", [])
    )


# ============================================================
# Recall@K
# ============================================================

def recall_at_k(
    gold_keys: set[str],
    results: list[dict],
    k: int,
) -> float:

    if not gold_keys:
        return 0.0

    retrieved = {
        chunk_key(result)
        for result in results[:k]
    }

    return float(
        bool(gold_keys & retrieved)
    )


# ============================================================
# MRR@K
# ============================================================

def reciprocal_rank(
    gold_keys: set[str],
    results: list[dict],
    k: int,
) -> float:

    if not gold_keys:
        return 0.0

    for rank, result in enumerate(
        results[:k],
        start=1,
    ):
        if chunk_key(result) in gold_keys:
            return 1.0 / rank

    return 0.0


# ============================================================
# NDCG@K
# ============================================================

def ndcg_at_k(
    gold_keys: set[str],
    results: list[dict],
    k: int,
) -> float:

    if not gold_keys:
        return 0.0

    # 검색 결과의 relevance
    relevance = []

    for result in results[:k]:

        key = chunk_key(result)

        relevance.append(
            1 if key in gold_keys else 0
        )

    # DCG
    dcg = 0.0

    for rank, rel in enumerate(
        relevance,
        start=1,
    ):

        if rel:
            dcg += (
                rel /
                math.log2(rank + 1)
            )

    # 이상적인 DCG
    ideal_relevance = sorted(
        relevance,
        reverse=True,
    )

    idcg = 0.0

    for rank, rel in enumerate(
        ideal_relevance,
        start=1,
    ):

        if rel:
            idcg += (
                rel /
                math.log2(rank + 1)
            )

    if idcg == 0:
        return 0.0

    return dcg / idcg


# ============================================================
# Pipeline 검색
# ============================================================

def retrieve(
    question: str,
    pipeline: str,
    region: str | None,
) -> list[dict]:

    with override_settings(
        RAG_PIPELINE=pipeline
    ):

        return service._retrieve(
            question,
            FETCH_K,

        )


# ============================================================
# Pipeline 평가
# ============================================================

def evaluate_pipeline(
    pipeline: str,
    qa_set: list[dict],
):

    print()
    print("=" * 80)
    print(f"Pipeline: {pipeline}")
    print("=" * 80)

    metric_values = {
        "recall@1": [],
        "recall@3": [],
        "recall@5": [],
        "mrr@5": [],
        "ndcg@5": [],
    }

    details = []

    latencies = []

    for item in qa_set:

        question = item["question"]

        region = item.get(
            "region"
        )

        gold_keys = get_gold_keys(item)

        # Gold가 없는 문항은 평가 제외
        if not gold_keys:
            continue

        start = time.perf_counter()

        try:

            results = retrieve(
                question,
                pipeline,
                region,
            )

            error = None

        except Exception as exc:

            results = []

            error = repr(exc)

        elapsed = (
            time.perf_counter()
            - start
        ) * 1000

        latencies.append(
            elapsed
        )

        # ----------------------------
        # Metrics
        # ----------------------------

        r1 = recall_at_k(
            gold_keys,
            results,
            1,
        )

        r3 = recall_at_k(
            gold_keys,
            results,
            3,
        )

        r5 = recall_at_k(
            gold_keys,
            results,
            5,
        )

        mrr = reciprocal_rank(
            gold_keys,
            results,
            5,
        )

        ndcg = ndcg_at_k(
            gold_keys,
            results,
            5,
        )

        metric_values[
            "recall@1"
        ].append(r1)

        metric_values[
            "recall@3"
        ].append(r3)

        metric_values[
            "recall@5"
        ].append(r5)

        metric_values[
            "mrr@5"
        ].append(mrr)

        metric_values[
            "ndcg@5"
        ].append(ndcg)

        # ----------------------------
        # 실제 순위 확인
        # ----------------------------

        ranks = []

        for rank, result in enumerate(
            results[:5],
            start=1,
        ):

            key = chunk_key(result)

            if key in gold_keys:
                ranks.append(rank)

        details.append({
            "pipeline": pipeline,
            "id": item.get("id"),
            "question": question,
            "gold": sorted(gold_keys),
            "retrieved": [
                chunk_key(r)
                for r in results[:5]
            ],
            "gold_ranks": ranks,
            "recall@1": r1,
            "recall@3": r3,
            "recall@5": r5,
            "mrr@5": mrr,
            "ndcg@5": ndcg,
            "latency_ms": round(
                elapsed,
                2,
            ),
            "error": error,
        })

        print(
            f"{item.get('id', ''):<6}"
            f"R@1={r1:.0f} "
            f"R@3={r3:.0f} "
            f"R@5={r5:.0f} "
            f"MRR={mrr:.3f} "
            f"NDCG={ndcg:.3f} "
            f"rank={ranks}"
        )

    # ========================================================
    # 평균
    # ========================================================

    def mean(values):

        if not values:
            return 0.0

        return sum(values) / len(values)

    summary = {
        "pipeline": pipeline,
        "questions": len(
            metric_values["recall@1"]
        ),
        "recall@1": mean(
            metric_values["recall@1"]
        ),
        "recall@3": mean(
            metric_values["recall@3"]
        ),
        "recall@5": mean(
            metric_values["recall@5"]
        ),
        "mrr@5": mean(
            metric_values["mrr@5"]
        ),
        "ndcg@5": mean(
            metric_values["ndcg@5"]
        ),
        "avg_ms": mean(
            latencies
        ),
    }

    return summary, details


# ============================================================
# Main
# ============================================================

def main():

    if not GOLD_PATH.exists():

        raise FileNotFoundError(
            f"{GOLD_PATH}가 없습니다.\n"
            "먼저 build_gold_chunks.py를 실행하세요."
        )

    qa_set = json.loads(
        GOLD_PATH.read_text(
            encoding="utf-8"
        )
    )

    print()
    print("=" * 90)
    print("Information Retrieval Evaluation")
    print("Vector vs BM25 vs Hybrid")
    print("=" * 90)

    print(
        f"전체 QA: {len(qa_set)}"
    )

    summaries = []

    all_details = []

    for pipeline in PIPELINES:

        summary, details = evaluate_pipeline(
            pipeline,
            qa_set,
        )

        summaries.append(
            summary
        )

        all_details.extend(
            details
        )

    # ========================================================
    # 최종 결과
    # ========================================================

    print()
    print()
    print("=" * 100)
    print("FINAL RESULT")
    print("=" * 100)

    print(
        f"{'Pipeline':<12}"
        f"{'R@1':>10}"
        f"{'R@3':>10}"
        f"{'R@5':>10}"
        f"{'MRR@5':>10}"
        f"{'NDCG@5':>10}"
        f"{'ms/query':>12}"
    )

    print("-" * 100)

    for result in summaries:

        print(
            f"{result['pipeline']:<12}"
            f"{result['recall@1']:>10.4f}"
            f"{result['recall@3']:>10.4f}"
            f"{result['recall@5']:>10.4f}"
            f"{result['mrr@5']:>10.4f}"
            f"{result['ndcg@5']:>10.4f}"
            f"{result['avg_ms']:>12.2f}"
        )

    print("=" * 100)

    # ========================================================
    # JSON
    # ========================================================

    RESULT_PATH.write_text(
        json.dumps(
            summaries,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    DETAIL_PATH.write_text(
        json.dumps(
            all_details,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(
        f"요약 결과: {RESULT_PATH}"
    )

    print(
        f"상세 결과: {DETAIL_PATH}"
    )


if __name__ == "__main__":
    main()