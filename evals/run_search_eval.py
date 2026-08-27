# path : evals/run_search_eval.py
"""
검색 파이프라인 비교 + 비용 지표.

    python -m evals.run_search_eval
    python -m evals.run_search_eval --pipelines legacy,bm25,hybrid
    python -m evals.run_search_eval --reverse       # 임베딩 캐시 영향 확인

■ run_eval_hybrid.py 와 무엇이 다른가

    run_eval_hybrid.py   답변 채점 (LLM 심판 + 규칙). 3차 원본 무수정.
    run_search_eval.py   검색 채점. 그 앞단.

답변 품질이 나쁠 때 원인이 "검색이 못 찾아서" 인지 "프롬프트가 못 써서"
인지 구분하려면 두 층을 나눠 재야 합니다. 3차에서 v6(Few-shot 추가)이
통과율을 올렸지만 검색 정확도는 81.0% 그대로였던 것이 그 예입니다.

■ 두 모드

    raw        _retrieve() 직접 호출. 순수 검색기 비교.
               owner/region 필터, 임계값, _apply_quota() 를 전부 건너뜁니다.
    balanced   search(balanced=True). 실제 답변에 쓰이는 근거.
               지역 필터 + 임계값 + 자리배분까지 포함합니다.

두 모드의 차이가 곧 "필터와 자리배분이 기여한 몫" 입니다.

■ 정답 판정

qa_set.json 에는 정답 청크 ID 가 없습니다(3차는 답변을 채점했으므로).
그래서 여기서는 **reference_answer 의 핵심 어휘가 검색된 청크 본문에
들어 있는가**로 봅니다. 정확한 청크 매칭이 아니라 근사치이며,
파이프라인 간 상대 비교용입니다. 절대 수치를 발표에 쓰지 마십시오.

    forbidden_mention 은 그대로 활용합니다 — 이건 3차가 정의한
    이 프로젝트 최대 위험(다른 지역 기준을 그 지역 것처럼 답하기)이고,
    검색 단계에서도 그대로 잽니다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import django

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.test import override_settings  # noqa: E402

from rag import service, usage  # noqa: E402

EVALS_DIR = BASE_DIR / "evals"
QA_SET = EVALS_DIR / "qa_set.json"
RESULT_PATH = EVALS_DIR / "search_eval_results.json"

DEFAULT_PIPELINES = ("legacy", "bm25", "hybrid")
MODES = ("raw", "balanced")
TOP_K = 5
FETCH_K = 100

TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
STOPWORDS = {
    "은", "는", "이", "가", "을", "를", "에", "의", "도", "로", "으로", "에서",
    "와", "과", "및", "합니다", "입니다", "있습니다", "해야", "하며", "다만",
    "경우", "또는", "그리고", "수", "것", "등", "따라", "위해", "때",
}


def keywords(text: str, limit: int = 12) -> list[str]:
    """모범답안에서 판정용 핵심 어휘를 뽑는다.

    2글자 이상, 스톱워드 제외, 등장 순서 유지. 흔한 조사·연결어를 빼면
    남는 것은 대체로 품목명·지역명·배출 방법 같은 판정 가능한 명사다.
    """
    out: list[str] = []
    for token in TOKEN_RE.findall(text or ""):
        if len(token) < 2 or token in STOPWORDS or token in out:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out


def coverage(reference: str, contents: list[str]) -> float:
    """검색된 본문이 모범답안 핵심 어휘를 얼마나 담고 있는가 (0~1)."""
    keys = keywords(reference)
    if not keys:
        return 0.0
    blob = " ".join(contents)
    return sum(1 for k in keys if k in blob) / len(keys)


def forbidden_hits(item: dict, contents: list[str]) -> list[str]:
    """다른 지역 기준이 근거에 섞여 들어왔는가."""
    blob = " ".join(contents)
    return [w for w in item.get("forbidden_mention", []) if w and w in blob]


# ---------------------------------------------------------------- 실행


def run_one(item: dict, mode: str) -> list[dict]:
    question = item["question"]
    region = item.get("region")
    if mode == "raw":
        return service._retrieve(question, FETCH_K, region=region)[:TOP_K]
    return service.search(
        question, owner_id=None, region=region, balanced=True
    )


def corpus_regions() -> set:
    """chunks.json 에 실제로 존재하는 region 값."""
    import json as _json
    path = settings.INDEX_DIR / "chunks.json"
    if not path.exists():
        return set()
    return {c.get("region") for c in _json.loads(path.read_text(encoding="utf-8"))}


def split_orphans(qa_set: list[dict]) -> tuple[list[dict], list[dict]]:
    """뒷받침 문서가 없는 지역 문항을 분리한다.

    문서가 0건인 지역의 문항은 어떤 파이프라인으로도 맞힐 수 없어
    모든 지표를 아래로 끌어내린다. 평가에서 빼는 것이 맞다.
    """
    have = corpus_regions() | {None, "", "common"}
    active, orphan = [], []
    for item in qa_set:
        if item.get("type") == "no_answer" or item.get("region") in have:
            active.append(item)
        else:
            orphan.append(item)
    return active, orphan


def prewarm(qa_set: list[dict]) -> None:
    """모든 질문을 미리 임베딩해 캐시를 채운다.

    이 단계가 없으면 첫 번째로 도는 파이프라인만 실제 API 호출 비용을
    뒤집어써서 지연시간 비교가 무의미해진다 (hybrid 가 legacy 를
    포함하는데도 더 빠르게 나오는 모순이 생긴다).
    """
    from rag import embeddings

    print("임베딩 캐시 선점...", end="", flush=True)
    start = time.perf_counter()
    for item in qa_set:
        try:
            embeddings.embed_query(item["question"])
        except Exception:
            pass
    print(f" {time.perf_counter() - start:.1f}s")


def evaluate(pipeline: str, mode: str, qa_set: list[dict]) -> tuple[dict, list[dict]]:
    covers: list[float] = []
    latencies: list[float] = []
    rows: list[dict] = []
    violation = 0
    empty_on_no_answer = 0
    no_answer_total = 0
    region_hit: list[float] = []
    no_answer_scores: list[dict] = []
    answerable_scores: list[dict] = []

    with override_settings(RAG_PIPELINE=pipeline):
        for _ in range(2):
            try:
                service._retrieve("종이컵 분리배출", 20)
            except Exception:
                pass

        for item in qa_set:
            answerable = item.get("type") != "no_answer"
            region = item.get("region")

            start = time.perf_counter()
            try:
                results = run_one(item, mode)
                error = None
            except Exception as exc:
                results, error = [], repr(exc)
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)

            contents = [r.get("content", "") for r in results]
            cov = coverage(item.get("reference_answer", ""), contents)
            bad = forbidden_hits(item, contents)

            if answerable:
                covers.append(cov)
                top = results[0] if results else {}
                answerable_scores.append({
                    "id": item.get("id"),
                    "score": top.get("score"),
                    "vector": top.get("vector_score"),
                    "bm25": top.get("bm25_score"),
                })
                if region and region != "common":
                    region_hit.append(
                        float(any(r.get("region") == region for r in results))
                    )
                if bad:
                    violation += 1
            else:
                no_answer_total += 1
                if not results:
                    empty_on_no_answer += 1
                top = results[0] if results else {}
                no_answer_scores.append({
                    "id": item.get("id"),
                    "n": len(results),
                    "score": top.get("score"),
                    "vector": top.get("vector_score"),
                    "bm25": top.get("bm25_score"),
                })

            rows.append({
                "pipeline": pipeline,
                "mode": mode,
                "id": item.get("id"),
                "type": item.get("type"),
                "region": region,
                "question": item.get("question"),
                "coverage": round(cov, 4),
                "forbidden_hits": bad,
                "n_results": len(results),
                "latency_ms": round(elapsed, 2),
                "top_titles": [r.get("title") for r in results[:3]],
                "error": error,
            })

    ordered = sorted(latencies)
    summary = {
        "pipeline": pipeline,
        "mode": mode,
        "scored": len(covers),
        "coverage": sum(covers) / len(covers) if covers else 0.0,
        "coverage_hit_rate": (
            sum(1 for c in covers if c >= 0.5) / len(covers) if covers else 0.0
        ),
        "region_doc_rate": (
            sum(region_hit) / len(region_hit) if region_hit else None
        ),
        "forbidden_violations": violation,
        "refusal_rate": (
            empty_on_no_answer / no_answer_total if no_answer_total else None
        ),
        "median_ms": statistics.median(latencies) if latencies else 0.0,
        "p95_ms": ordered[int(len(ordered) * 0.95) - 1] if ordered else 0.0,
        "no_answer_scores": no_answer_scores,
        "answerable_scores": answerable_scores,
    }
    return summary, rows


# ---------------------------------------------------------------- 비용


def measure_cost(qa_set: list[dict], pipeline: str, sample: int) -> dict:
    """질문 1건당 비용을 잰다.

    캐시가 채워진 상태에서는 임베딩 비용이 0 으로 나온다. 그게 정상이며
    캐시의 효과 그 자체다. '캐시 미적중 시' 비용을 보려면
    var/embed_cache.json (또는 EMBED_CACHE 경로)을 지우고 다시 돌릴 것.
    """
    subset = qa_set[:sample]
    per_question: list[dict] = []

    with override_settings(RAG_PIPELINE=pipeline):
        for item in subset:
            with usage.collect() as meter:
                try:
                    service.ask(
                        item["question"], owner_id=None, region=item.get("region")
                    )
                except Exception:
                    pass
            row = meter.as_dict()
            row["id"] = item.get("id")
            per_question.append(row)

    priced = [r for r in per_question if r["cost_usd"] is not None]
    total = sum(r["cost_usd"] for r in priced) if priced else None
    unpriced = sorted({m for r in per_question for m in r["unpriced_models"]})

    return {
        "pipeline": pipeline,
        "questions": len(subset),
        "input_tokens": sum(r["input_tokens"] for r in per_question),
        "output_tokens": sum(r["output_tokens"] for r in per_question),
        "api_calls": sum(r["api_calls"] for r in per_question),
        "cached_calls": sum(r["cached_calls"] for r in per_question),
        "total_cost_usd": total,
        "cost_per_question_usd": (total / len(subset)) if total is not None else None,
        "unpriced_models": unpriced,
        "per_question": per_question,
    }


def measure_index_cost() -> dict:
    """재색인 1회 비용. 문서 임베딩이 대부분을 차지한다."""
    with usage.collect() as meter:
        result = service.rebuild_index()
    row = meter.as_dict()
    row["indexed_chunks"] = result.get("indexed_chunks")
    return row


# ---------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipelines", default=",".join(DEFAULT_PIPELINES))
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--no-prewarm", action="store_true")
    parser.add_argument("--cost-sample", type=int, default=0,
                        help="비용 측정에 쓸 문항 수 (LLM API 호출 발생). 0 이면 생략")
    parser.add_argument("--include-orphans", action="store_true",
                        help="문서가 없는 지역 문항도 포함 (기본은 제외)")
    parser.add_argument("--index-cost", action="store_true",
                        help="재색인 1회 비용도 측정 (임베딩 API 호출 발생)")
    args = parser.parse_args()

    pipelines = tuple(p.strip() for p in args.pipelines.split(",") if p.strip())
    if args.reverse:
        pipelines = tuple(reversed(pipelines))

    if not QA_SET.exists():
        sys.exit(f"[중단] {QA_SET} 가 없습니다.")

    qa_set = json.loads(QA_SET.read_text(encoding="utf-8"))

    if not args.include_orphans:
        qa_set, orphan = split_orphans(qa_set)
        if orphan:
            import collections as _c
            dist = _c.Counter(x.get("region") for x in orphan)
            print(f"[제외] 뒷받침 문서가 없는 지역 문항 {len(orphan)}건: {dict(dist)}")
            print("       (포함하려면 --include-orphans)")

    answerable = [x for x in qa_set if x.get("type") != "no_answer"]

    print("=" * 92)
    print("검색 파이프라인 비교")
    print("=" * 92)
    print(f"문항 {len(qa_set)} (answerable {len(answerable)} / "
          f"no_answer {len(qa_set) - len(answerable)})")
    print(f"BACKEND={settings.EMBEDDING_BACKEND}  "
          f"MIN_SCORE={settings.RAG_MIN_SCORE}  "
          f"MIN_SCORE_BM25={getattr(settings, 'RAG_MIN_SCORE_BM25', '(미설정)')}  "
          f"RRF_K={getattr(settings, 'RAG_RRF_K', 60)}")
    print(f"실행 순서: {' -> '.join(pipelines)}")

    if not args.no_prewarm:
        prewarm(qa_set)

    summaries: list[dict] = []
    details: list[dict] = []
    for pipeline in pipelines:
        for mode in MODES:
            summary, rows = evaluate(pipeline, mode, qa_set)
            summaries.append(summary)
            details.extend(rows)

    order = {p: i for i, p in enumerate(DEFAULT_PIPELINES)}
    summaries.sort(key=lambda s: (order.get(s["pipeline"], 99), MODES.index(s["mode"])))

    head = (f"{'Pipeline':<10}{'Mode':<11}{'근거커버':>9}{'적중률':>8}"
            f"{'지역문서':>9}{'금지어':>8}{'거절률':>8}{'med ms':>9}{'p95 ms':>9}")
    print()
    print(head)
    print("-" * len(head))
    prev = None
    for s in summaries:
        if prev and s["pipeline"] != prev:
            print("-" * len(head))
        prev = s["pipeline"]
        rd = "-" if s["region_doc_rate"] is None else f"{s['region_doc_rate']:.3f}"
        rr = "-" if s["refusal_rate"] is None else f"{s['refusal_rate']:.2f}"
        print(f"{s['pipeline']:<10}{s['mode']:<11}"
              f"{s['coverage']:>9.3f}{s['coverage_hit_rate']:>8.3f}"
              f"{rd:>9}{s['forbidden_violations']:>8}{rr:>8}"
              f"{s['median_ms']:>9.1f}{s['p95_ms']:>9.1f}")

    print()
    print("  근거커버 = 모범답안 핵심 어휘가 검색 본문에 담긴 비율 (근사 지표)")
    print("  적중률   = 근거커버 0.5 이상인 문항 비율")
    print("  지역문서 = 지역 문항에서 해당 지역 청크가 결과에 든 비율")
    print("  금지어   = 다른 지역 기준이 근거에 섞인 문항 수 (0 이어야 함)")
    print("  거절률   = no_answer 문항에서 결과 0건 반환 비율 (1.00 이 이상적)")

    print()
    print("=" * 92)
    print("[임계값 튜닝] no_answer 문항의 최고 점수 (raw 모드)")
    print("=" * 92)
    for s in summaries:
        if s["mode"] != "raw" or not s["no_answer_scores"]:
            continue
        print(f"\n  {s['pipeline']}")
        for r in s["no_answer_scores"]:
            print(f"    {str(r['id']):<6} n={r['n']:<3} score={r['score']}  "
                  f"vector={r['vector']}  bm25={r['bm25']}")
    print()
    print("  [비교] answerable 문항의 최고 점수 분포 (raw 모드)")
    print("  두 분포가 겹치면 임계값 하나로는 거를 수 없다는 뜻입니다.")
    for s_ in summaries:
        if s_["mode"] != "raw" or not s_.get("answerable_scores"):
            continue
        for field in ("score", "bm25", "vector"):
            vals = sorted(
                v[field] for v in s_["answerable_scores"] if v.get(field) is not None
            )
            if not vals:
                continue
            bad = sorted(
                v[field] for v in s_["no_answer_scores"] if v.get(field) is not None
            )
            print(f"\n    {s_['pipeline']} · {field}")
            print(f"      answerable  최저 {vals[0]:.4f} / "
                  f"10%tile {vals[max(0, len(vals) // 10)]:.4f} / "
                  f"중앙 {vals[len(vals) // 2]:.4f}")
            if bad:
                print(f"      no_answer   최고 {bad[-1]:.4f} / 중앙 {bad[len(bad) // 2]:.4f}")
                gap = vals[0] - bad[-1]
                verdict = ("분리 가능 -> 임계값 후보 "
                           f"{(vals[0] + bad[-1]) / 2:.4f}") if gap > 0 else "두 분포가 겹침 -> 임계값으로 분리 불가"
                print(f"      판정: {verdict}")

    payload = {
        "config": {
            "embedding_backend": settings.EMBEDDING_BACKEND,
            "min_score": settings.RAG_MIN_SCORE,
            "min_score_bm25": getattr(settings, "RAG_MIN_SCORE_BM25", None),
            "rrf_k": getattr(settings, "RAG_RRF_K", 60),
        },
        "summaries": summaries,
        "details": details,
    }

    # ------------------------------------------------------------ 비용
    if args.index_cost:
        print()
        print("=" * 92)
        print("[비용] 재색인 1회")
        print("=" * 92)
        index_cost = measure_index_cost()
        payload["index_cost"] = index_cost
        print(f"  청크 {index_cost['indexed_chunks']}개")
        print(f"  API 호출 {index_cost['api_calls']}회 / 캐시 적중 {index_cost['cached_calls']}회")
        print(f"  입력 토큰 {index_cost['input_tokens']:,}")
        cost = index_cost["cost_usd"]
        print(f"  비용 ${cost:.6f}" if cost is not None
              else f"  비용 측정 불가 — 가격표에 없는 모델: {index_cost['unpriced_models']}")

    if args.cost_sample > 0:
        print()
        print("=" * 92)
        print(f"[비용] 질문 {args.cost_sample}건 (LLM 호출 발생)")
        print("=" * 92)
        costs = []
        for pipeline in pipelines:
            c = measure_cost(qa_set, pipeline, args.cost_sample)
            costs.append(c)
            per = c["cost_per_question_usd"]
            print(f"\n  {pipeline}")
            print(f"    API 호출 {c['api_calls']}회 / 캐시 적중 {c['cached_calls']}회")
            print(f"    입력 {c['input_tokens']:,} / 출력 {c['output_tokens']:,} 토큰")
            if per is None:
                print(f"    비용 측정 불가 — 가격표에 없는 모델: {c['unpriced_models']}")
            else:
                print(f"    합계 ${c['total_cost_usd']:.6f} / "
                      f"질문당 ${per:.6f} (1,000건당 ${per * 1000:.2f})")
        payload["query_cost"] = costs

    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(f"결과 저장: {RESULT_PATH}")
    print()
    print("주의:")
    print("- 근거커버는 어휘 포함 여부에 기반한 근사 지표입니다.")
    print("  파이프라인 간 상대 비교용이며 절대 수치를 발표에 쓰지 마십시오.")
    print("- 3차 지표와 비교할 때는 RAG_PIPELINE=legacy 로 먼저 재측정하십시오.")


if __name__ == "__main__":
    main()