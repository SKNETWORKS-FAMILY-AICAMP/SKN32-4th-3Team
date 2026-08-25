# path : evals/run_eval_hybrid.py
"""
[RAG 파트] LLM + 규칙 혼합 평가 시스템.

    python -m evals.run_eval_hybrid

평가 방식
  ① 정답 정확도 (LLM 판정)
     GPT가 모범답안 대비 챗봇 답변의 의미적 정확도를 0~5점으로 채점.
     키워드 매칭이 아니라 의미가 맞으면 인정한다.

  ② 환각 검출 (규칙 기반)
     forbidden_mention 키워드가 답변에 포함되면 환각으로 판정.
     타 지역 정보를 해당 지역인 것처럼 안내하는 경우 등을 잡는다.

  ③ 거부 정확도 (규칙 기반)
     should_have_answer=false 인 질문에 챗봇이 거부했는지 확인.
     거부해야 하는데 답변을 생성하면 환각으로 처리.

채점 기준 (correctness_score)
  5: 완벽 — 모범답안의 핵심 정보를 모두 포함
  4: 우수 — 핵심 정보 대부분 포함, 사소한 누락
  3: 보통 — 핵심 정보 절반 이상 포함
  2: 미흡 — 일부만 맞고 중요한 내용 누락
  1: 부족 — 거의 관련 없는 답변
  0: 오답 — 완전히 틀리거나 무관한 답변
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import django

# Django ORM·settings 를 스크립트에서 쓰기 위한 부트스트랩.
# `from django.conf import settings` 접근보다 먼저 실행돼야 합니다.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from openai import OpenAI  # noqa: E402

from django.conf import settings  # noqa: E402  (3차: from app.core.config import settings)
from rag import service as rag_service  # noqa: E402  (3차: from app.services import rag_service)

EVALS_DIR = Path(__file__).resolve().parent
QA_SET = EVALS_DIR / "qa_set.json"

# OpenAI 클라이언트
client = OpenAI(api_key=settings.OPENAI_API_KEY)
LLM_MODEL = settings.OPENAI_MODEL  # gpt-4o-mini


# ─────────────────── LLM 채점 ───────────────────


JUDGE_PROMPT = """\
당신은 환경/분리배출 챗봇의 답변 품질을 평가하는 채점관입니다.

## 평가 대상
- 질문: {question}
- 모범답안: {reference}
- 챗봇 답변: {answer}

## 채점 기준
답변이 모범답안의 핵심 정보를 얼마나 정확히 전달하는지 0~5점으로 채점하세요.
표현이 달라도 의미가 같으면 정답으로 인정합니다.
(예: "헹구다" = "세척하다" = "씻다", "종량제봉투" = "일반쓰레기봉투")

5점: 핵심 정보를 모두 정확히 포함
4점: 핵심 정보 대부분 포함, 사소한 누락
3점: 핵심 정보 절반 이상 포함
2점: 일부만 맞고 중요한 내용 누락
1점: 거의 관련 없는 답변
0점: 완전히 틀리거나 무관한 답변

## 출력 형식 (반드시 이 형식만 출력)
SCORE: [0-5]
REASON: [한 줄 사유]
"""


def llm_judge(question: str, reference: str, answer: str) -> dict:
    """GPT로 답변 정확도를 채점한다."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        reference=reference,
        answer=answer,
    )

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
        )
        text = resp.choices[0].message.content.strip()

        # SCORE: N 파싱
        score_match = re.search(r"SCORE:\s*(\d)", text)
        reason_match = re.search(r"REASON:\s*(.+)", text)

        score = int(score_match.group(1)) if score_match else -1
        reason = reason_match.group(1).strip() if reason_match else text

        return {"score": score, "reason": reason, "raw": text}
    except Exception as e:
        return {"score": -1, "reason": f"LLM 호출 실패: {e}", "raw": ""}


# ─────────────────── 규칙 기반 검사 ───────────────────


REFUSAL_MARKERS = (
    "찾을 수 없",
    "보유하고 있지 않",
    "자료가 없",
    "확인하셔야",
    "안내드리기 어렵",
    "제공하기 어렵",
    "답변드리기 어렵",
    "관련 정보가 없",
    "해당 정보를 찾",
    "데이터가 없",
    "모르겠",
    "알 수 없",
    "범위를 벗어",
    "관련된 자료",
    "드리기 어렵",
    "포함되어 있지 않",
    "포함하고 있지 않",
    "정보가 없",
    "확인되지 않",
    "안내해 드리기",
    "제공해 드리기",
    "말씀드리기 어렵",
)


def is_refusal(text: str) -> bool:
    """거부 답변인지 판단한다."""
    return any(m in text for m in REFUSAL_MARKERS)


def check_forbidden(item: dict, text: str) -> list[str]:
    """금지 키워드가 부적절하게 사용되었는지 LLM으로 문맥 판단한다.

    단순 문자열 포함이 아니라, 해당 키워드를 '권장'하는 맥락인지 확인한다.
    "종량제봉투에 버리면 안 됩니다" 처럼 경고 목적이면 환각이 아니다.
    """
    candidates = [k for k in item.get("forbidden_mention", []) if k in text]
    if not candidates:
        return []

    # LLM으로 문맥 판단
    prompt = f"""다음 챗봇 답변에서 아래 키워드들이 '잘못된 정보를 전달하는 맥락'으로 사용되었는지 판단하세요.

질문: {item['question']}
답변: {text}

키워드: {candidates}

각 키워드에 대해:
- 해당 키워드를 '하라고 권장'하거나 '올바른 방법인 것처럼 안내'하면 → 부적절
- 해당 키워드를 '하면 안 된다고 경고'하거나 '금지 사항으로 언급'하면 → 적절

부적절하게 사용된 키워드만 쉼표로 나열하세요. 모두 적절하면 "없음"이라고 답하세요.

출력 형식:
INAPPROPRIATE: [키워드1, 키워드2] 또는 없음"""

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        result_text = resp.choices[0].message.content.strip()

        if "없음" in result_text:
            return []

        # 부적절 판정된 키워드만 반환
        return [k for k in candidates if k in result_text]
    except Exception:
        # LLM 실패 시 기존 방식으로 fallback
        return candidates


# ─────────────────── 종합 채점 ───────────────────


def grade(item: dict) -> dict:
    """한 문항을 실행하고 LLM+규칙으로 채점한다."""
    region = item.get("region")

    # 챗봇 호출
    search_results = rag_service.search(
        item["question"], owner_id=None, region=region, balanced=True
    )
    contexts = [r["content"] for r in search_results if r.get("content")]

    result = rag_service.ask(item["question"], owner_id=None, region=region)
    answer = result.get("answer", "")
    tip = result.get("tip", "")
    sources = result.get("sources", [])

    full_text = f"{answer}\n{tip}".strip()

    should_answer = item.get("should_have_answer", True)
    reference = item.get("reference_answer", "")
    refused = is_refusal(full_text)
    forbidden = check_forbidden(item, full_text)

    # ── 평가 분기 ──

    if should_answer:
        # 답변해야 하는 질문
        if refused:
            # 답변 가능한데 거부함 → 오거부
            correctness = {"score": 0, "reason": "답변 가능한 질문인데 거부함 (오거부)", "raw": ""}
            hallucinated = False
            verdict = "FAIL"
        else:
            # LLM이 정확도 채점
            correctness = llm_judge(item["question"], reference, full_text)
            hallucinated = bool(forbidden)

            score = correctness["score"]
            if score >= 4:
                # 높은 정확도면 금지 키워드가 있어도 PASS (경고만 표시)
                verdict = "PASS"
            elif score == 3:
                # 보통 정확도면 환각 여부에 따라 판정
                verdict = "FAIL" if hallucinated else "PASS"
            else:
                verdict = "FAIL"
    else:
        # 답변하면 안 되는 질문
        if refused:
            # 거부를 올바르게 했으면 PASS (부가 정보 언급은 경고만)
            correctness = {"score": 5, "reason": "자료 없는 질문을 올바르게 거부", "raw": ""}
            hallucinated = False
            verdict = "PASS"
        else:
            correctness = {"score": 0, "reason": "자료 없는 질문에 답변을 생성함 (환각)", "raw": ""}
            hallucinated = True
            verdict = "FAIL"

    return {
        "id": item["id"],
        "type": item["type"],
        "region": region,
        "question": item["question"],
        "reference_answer": reference,
        "answer": answer,
        "tip": tip,
        "sources": [s.get("title", "") for s in sources],
        "contexts": contexts,
        # 채점 결과
        "correctness_score": correctness["score"],
        "correctness_reason": correctness["reason"],
        "refused": refused,
        "forbidden_used": forbidden,
        "hallucinated": hallucinated,
        "verdict": verdict,
    }


# ─────────────────── 집계 ───────────────────


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["verdict"] == "PASS")

    answerable = [r for r in results if r["type"] != "no_answer"]
    no_answer = [r for r in results if r["type"] == "no_answer"]

    scores = [r["correctness_score"] for r in answerable if r["correctness_score"] >= 0]

    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0,
        "avg_correctness": round(sum(scores) / len(scores), 2) if scores else None,
        "hallucination_count": sum(1 for r in results if r["hallucinated"]),
        "hallucination_rate": round(
            sum(1 for r in results if r["hallucinated"]) / total, 4
        ) if total else 0,
        "refusal_accuracy": round(
            sum(1 for r in no_answer if r["refused"]) / len(no_answer), 4
        ) if no_answer else None,
        "false_refusal_count": sum(
            1 for r in answerable if r["refused"]
        ),
    }


def by_group(results: list[dict], key: str) -> dict:
    grouped: dict[str, dict] = {}
    for r in results:
        k = r.get(key) or "없음"
        bucket = grouped.setdefault(k, {"total": 0, "passed": 0, "scores": []})
        bucket["total"] += 1
        if r["verdict"] == "PASS":
            bucket["passed"] += 1
        if r["correctness_score"] >= 0:
            bucket["scores"].append(r["correctness_score"])
    for bucket in grouped.values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["total"], 4)
        bucket["avg_score"] = (
            round(sum(bucket["scores"]) / len(bucket["scores"]), 2)
            if bucket["scores"] else None
        )
        del bucket["scores"]
    return grouped


# ─────────────────── 실행 ───────────────────


REGION_LABELS = {
    "seoul": "서울", "cheonan": "천안", "busan_namgu": "부산 남구",
    "incheon_michuhol": "인천 미추홀", "sejong": "세종", "jeju": "제주",
}


def main() -> None:
    if not QA_SET.exists():
        print(f"[중단] 정답셋이 없습니다: {QA_SET}")
        return

    items = json.loads(QA_SET.read_text(encoding="utf-8"))

    config = {
        "eval_method": "llm_hybrid",
        "llm_model": LLM_MODEL,
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "rag_min_score": settings.RAG_MIN_SCORE,
        "rag_top_k": settings.RAG_TOP_K,
        "chunk_size": settings.CHUNK_SIZE,
        "chunk_overlap": settings.CHUNK_OVERLAP,
    }

    print("=" * 56)
    print("  EcoBot LLM + 규칙 혼합 평가")
    print("=" * 56)
    for k, v in config.items():
        print(f"  {k:20} {v}")
    print(f"\n  {len(items)}문항 실행\n")

    results: list[dict] = []
    for i, item in enumerate(items, 1):
        row = grade(item)
        results.append(row)

        reg = REGION_LABELS.get(row["region"], row["region"] or "전체")
        score = row["correctness_score"]
        flags = []
        if row["hallucinated"]:
            flags.append("환각")
        if row["refused"] and item.get("should_have_answer", True):
            flags.append("오거부")
        flag_str = f" [{','.join(flags)}]" if flags else ""

        print(
            f"  {i:2}/{len(items)} {row['verdict']:4} "
            f"[{reg:<6}] 점수:{score}/5  "
            f"{row['question'][:28]}{flag_str}"
        )
        if row["correctness_reason"]:
            print(f"        → {row['correctness_reason'][:60]}")

        # API rate limit 방지
        time.sleep(0.3)

    summary = summarize(results)
    types = by_group(results, "type")
    regions = by_group(results, "region")

    print(f"\n{'─' * 56}")
    print(f"  통과율           {summary['passed']}/{summary['total']}  ({summary['pass_rate']:.1%})")
    print(f"  평균 정확도 점수 {summary['avg_correctness']}/5")
    print(f"  환각             {summary['hallucination_count']}건  ({summary['hallucination_rate']:.1%})")
    print(f"  거부 정확도      {summary['refusal_accuracy']:.1%}" if summary["refusal_accuracy"] is not None else "  거부 정확도      N/A")
    print(f"  오거부           {summary['false_refusal_count']}건")
    print(f"{'─' * 56}")

    print("\n  [유형별]")
    for name, bucket in types.items():
        print(f"    {name:<16} {bucket['passed']}/{bucket['total']}  "
              f"({bucket['pass_rate']:.1%})  avg={bucket['avg_score']}")

    print("\n  [지역별]")
    for name, bucket in regions.items():
        label = REGION_LABELS.get(name, name)
        print(f"    {label:<14} {bucket['passed']}/{bucket['total']}  "
              f"({bucket['pass_rate']:.1%})")

    # 결과 저장
    out_path = EVALS_DIR / f"hybrid_results_{datetime.now():%Y%m%d_%H%M}.json"
    out_path.write_text(
        json.dumps(
            {
                "measured_at": datetime.now().isoformat(timespec="seconds"),
                "config": config,
                "summary": summary,
                "by_type": types,
                "by_region": regions,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
