# path : evals/run_report.py
"""
[RAG 파트] LLM 혼합 평가 결과 리포트 생성.

    python -m evals.run_report                          # 최근 결과
    python -m evals.run_report hybrid_results_YYYYMMDD_HHMM.json

run_eval_hybrid.py 결과 JSON을 읽어 상세 리포트를 출력한다.
챗봇을 호출하지 않으므로 반복 분석 가능.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent

REGION_LABELS = {
    "seoul": "서울", "cheonan": "천안", "busan_namgu": "부산 남구",
    "incheon_michuhol": "인천 미추홀", "sejong": "세종", "jeju": "제주",
    "없음": "미지정",
}

TYPE_LABELS = {
    "single_fact": "단일 사실", "region_specific": "지역 특화",
    "cross_reference": "교차 참조", "no_answer": "자료 없음",
}


def load_results(name: str | None) -> tuple[Path, dict]:
    if name:
        path = EVALS_DIR / name
    else:
        candidates = sorted(EVALS_DIR.glob("hybrid_results_*.json"))
        if not candidates:
            print("[중단] hybrid_results_*.json 이 없습니다. 먼저 run_eval_hybrid 를 실행하세요.")
            sys.exit(1)
        path = candidates[-1]
    return path, json.loads(path.read_text(encoding="utf-8"))


def score_distribution(results: list[dict]) -> dict[str, int]:
    """정확도 점수 분포."""
    dist = {f"{i}점": 0 for i in range(6)}
    for r in results:
        s = r.get("correctness_score", -1)
        if 0 <= s <= 5:
            dist[f"{s}점"] += 1
    return dist


def classify_failures(results: list[dict]) -> list[dict]:
    failures = []
    for r in results:
        if r["verdict"] == "PASS":
            continue
        reasons = []
        if r.get("hallucinated"):
            reasons.append("환각")
        if r.get("forbidden_used"):
            reasons.append(f"금지 키워드: {r['forbidden_used']}")
        if r.get("refused") and r.get("type") != "no_answer":
            reasons.append("오거부")
        if not r.get("refused") and r.get("type") == "no_answer":
            reasons.append("미거부 (답변 생성)")
        score = r.get("correctness_score", -1)
        if score < 3 and r.get("type") != "no_answer" and not r.get("refused"):
            reasons.append(f"낮은 정확도 ({score}/5)")

        preview = r.get("answer", "")
        if len(preview) > 80:
            preview = preview[:80] + "…"

        failures.append({
            "id": r["id"],
            "type": r["type"],
            "region": r.get("region"),
            "question": r["question"],
            "reasons": reasons,
            "score": score,
            "llm_reason": r.get("correctness_reason", ""),
            "answer_preview": preview,
        })
    return failures


def print_report(path: Path, data: dict, results: list[dict]) -> None:
    config = data.get("config", {})
    summary = data.get("summary", {})
    W = 60

    print(f"\n{'=' * W}")
    print(f"  EcoBot 답변 품질 평가 리포트 (LLM + 규칙 혼합)")
    print(f"{'=' * W}")
    print(f"  대상 파일  : {path.name}")
    print(f"  측정 시각  : {data.get('measured_at', '?')}")
    print(f"  평가 모델  : {config.get('llm_model', '?')}")
    print(f"  임베딩     : {config.get('embedding_backend', '?')}")
    print(f"  유사도 임계: {config.get('rag_min_score', '?')}")
    print()

    # ── 1. 종합 ──
    print(f"{'─' * W}")
    print(f"  1. 종합 성적")
    print(f"{'─' * W}")
    print(f"  통과율           {summary.get('passed', 0)}/{summary.get('total', 0)}  "
          f"({summary.get('pass_rate', 0):.1%})")
    avg_c = summary.get("avg_correctness")
    print(f"  평균 정확도      {avg_c}/5" if avg_c is not None else "  평균 정확도      N/A")
    print(f"  환각             {summary.get('hallucination_count', 0)}건  "
          f"({summary.get('hallucination_rate', 0):.1%})")
    ra = summary.get("refusal_accuracy")
    print(f"  거부 정확도      {ra:.1%}" if ra is not None else "  거부 정확도      N/A")
    print(f"  오거부           {summary.get('false_refusal_count', 0)}건")
    print()

    # ── 2. 점수 분포 ──
    print(f"{'─' * W}")
    print(f"  2. 정확도 점수 분포")
    print(f"{'─' * W}")
    dist = score_distribution(results)
    max_count = max(dist.values()) or 1
    for label, count in sorted(dist.items(), reverse=True):
        bar = "█" * int(count / max_count * 25)
        print(f"  {label:>4} | {bar:<25} {count}개")
    print()

    # ── 3. 유형별 ──
    print(f"{'─' * W}")
    print(f"  3. 유형별 성적")
    print(f"{'─' * W}")
    by_type = data.get("by_type", {})
    for t, bucket in sorted(by_type.items()):
        label = TYPE_LABELS.get(t, t)
        print(f"  {label:<12} {bucket['passed']}/{bucket['total']}  "
              f"({bucket['pass_rate']:.1%})  avg={bucket.get('avg_score', 'N/A')}")
    print()

    # ── 4. 지역별 ──
    print(f"{'─' * W}")
    print(f"  4. 지역별 성적")
    print(f"{'─' * W}")
    by_region = data.get("by_region", {})
    for reg, bucket in sorted(by_region.items()):
        label = REGION_LABELS.get(reg, reg)
        print(f"  {label:<14} {bucket['passed']}/{bucket['total']}  "
              f"({bucket['pass_rate']:.1%})")
    print()

    # ── 5. 실패 분석 ──
    failures = classify_failures(results)
    print(f"{'─' * W}")
    print(f"  5. 실패 문항 분석 ({len(failures)}건)")
    print(f"{'─' * W}")
    if not failures:
        print("  전부 통과!")
    else:
        for f in failures:
            reg = REGION_LABELS.get(f["region"], f["region"] or "")
            typ = TYPE_LABELS.get(f["type"], f["type"])
            print(f"\n  [{f['id']}] {f['question']}")
            print(f"    유형: {typ}  지역: {reg}  점수: {f['score']}/5")
            for reason in f["reasons"]:
                print(f"    ✗ {reason}")
            if f["llm_reason"]:
                print(f"    LLM: {f['llm_reason'][:70]}")
            print(f"    답변: {f['answer_preview']}")

    # ── 6. 실패 원인 요약 ──
    print(f"\n{'─' * W}")
    print(f"  6. 실패 원인 빈도")
    print(f"{'─' * W}")
    reason_count: dict[str, int] = defaultdict(int)
    for f in failures:
        for reason in f["reasons"]:
            category = reason.split(":")[0].split("(")[0].strip()
            reason_count[category] += 1
    if reason_count:
        for category, count in sorted(reason_count.items(), key=lambda x: -x[1]):
            print(f"  {category:<30} {count}건")
    else:
        print("  (실패 없음)")

    print(f"\n{'=' * W}\n")


def save_report(path: Path, data: dict, results: list[dict]) -> Path:
    failures = classify_failures(results)

    report = {
        "measured_at": data.get("measured_at"),
        "source_file": path.name,
        "config": data.get("config"),
        "summary": data.get("summary"),
        "score_distribution": score_distribution(results),
        "by_type": data.get("by_type"),
        "by_region": data.get("by_region"),
        "failures": failures,
    }

    out = EVALS_DIR / f"hybrid_report_{datetime.now():%Y%m%d_%H%M}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else None
    path, data = load_results(name)
    results = data["results"]

    print_report(path, data, results)

    out = save_report(path, data, results)
    print(f"리포트 저장: {out}")


if __name__ == "__main__":
    main()
