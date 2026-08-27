"""qa_set.json의 reference_answer를 이용해 정답 chunk를 자동 추정한다.

주의:
- 이것은 사람이 라벨링한 gold가 아니라 '자동 pseudo-gold'다.
- gold 선정 기준의 대부분이 어휘 중첩이므로, 이 gold로 평가하면
  어휘 매칭 계열(BM25/Hybrid)이 구조적으로 유리하다. 결과 해석 시 반드시 감안할 것.

변경점:
1. should_have_answer=false 질문에는 gold를 만들지 않는다.
   (기존에는 threshold만 넘으면 '오늘 날씨 어때?'에도 gold가 붙어
    엉뚱한 청크를 찾아오는 것이 정답으로 채점됐다)
2. overlap을 F-beta로 바꿔 긴 청크가 무조건 유리하던 편향을 줄였다.
   (기존 overlap = |a∩b| / |a| 는 분모가 정답 토큰이라 청크가 길수록 점수가 올랐다)
3. region을 보너스가 아니라 하드 필터로 적용한다.
4. 1위와 2위 점수 차가 작으면 needs_review 플래그를 세워 수동 검수 대상을 좁힌다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
QA_PATH = BASE_DIR / "evals" / "qa_set.json"
CHUNK_PATH = BASE_DIR / "vector_db" / "chunks.json"
OUTPUT_PATH = BASE_DIR / "evals" / "qa_set_with_gold.json"
REPORT_PATH = BASE_DIR / "evals" / "gold_candidates.json"

TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
STOPWORDS = {
    "은", "는", "이", "가", "을", "를", "에", "의", "도", "로", "으로",
    "에서", "와", "과", "및", "하다", "합니다", "인가요", "어떻게", "어디",
    "무엇", "경우", "후", "전", "것", "수", "있습니다", "버리나요", "배출",
}

MIN_SCORE = 0.12
GOLD_RATIO = 0.85
MAX_GOLD = 3
REVIEW_MARGIN = 0.05  # 1위와 2위 차이가 이보다 작으면 수동 검수 대상

COMMON_REGIONS = (None, "", "common", "national")


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def tokens(text: str) -> set[str]:
    return {
        t for t in TOKEN_RE.findall((text or "").lower())
        if len(t) >= 2 and t not in STOPWORDS
    }


def char_ngrams(text: str, n: int = 2) -> set[str]:
    s = normalize(text)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def fbeta_overlap(reference: set[str], candidate: set[str], beta: float = 2.0) -> float:
    """정답 토큰 커버리지(recall)를 중시하되 청크 길이도 벌점으로 반영한다.

    recall    = 정답 토큰 중 청크가 담고 있는 비율
    precision = 청크 토큰 중 정답과 겹치는 비율  <- 긴 청크에 대한 벌점 역할
    beta=2 이므로 recall에 4배 가중.
    """
    if not reference or not candidate:
        return 0.0
    inter = len(reference & candidate)
    if inter == 0:
        return 0.0
    recall = inter / len(reference)
    precision = inter / len(candidate)
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def region_allows(chunk_region, qa_region) -> bool:
    if qa_region in COMMON_REGIONS:
        return True
    if chunk_region == qa_region:
        return True
    return chunk_region in COMMON_REGIONS


def score_chunk(qa: dict, chunk: dict) -> tuple[float, dict]:
    question = qa.get("question", "")
    answer = qa.get("reference_answer", "")
    content = chunk.get("content", "")

    q_tokens, a_tokens, c_tokens = tokens(question), tokens(answer), tokens(content)
    q_char, a_char, c_char = char_ngrams(question), char_ngrams(answer), char_ngrams(content)

    answer_token = fbeta_overlap(a_tokens, c_tokens)
    answer_char = fbeta_overlap(a_char, c_char)
    question_token = fbeta_overlap(q_tokens, c_tokens)
    question_char = fbeta_overlap(q_char, c_char)

    score = (
        0.45 * answer_token
        + 0.35 * answer_char
        + 0.12 * question_token
        + 0.05 * question_char
    )

    details = {
        "answer_token_f2": round(answer_token, 4),
        "answer_char_f2": round(answer_char, 4),
        "question_token_f2": round(question_token, 4),
        "question_char_f2": round(question_char, 4),
        "chunk_len": len(content),
    }
    return score, details


def main() -> None:
    if not QA_PATH.exists():
        raise FileNotFoundError(f"QA 파일이 없습니다: {QA_PATH}")
    if not CHUNK_PATH.exists():
        raise FileNotFoundError(
            f"chunks.json이 없습니다: {CHUNK_PATH}\n"
            "먼저 python manage.py rag_reindex 로 인덱스를 생성하세요."
        )

    qa_set = json.loads(QA_PATH.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNK_PATH.read_text(encoding="utf-8"))

    corpus_regions = {c.get("region") for c in chunks}
    output, report = [], []
    review_count = 0

    for qa in qa_set:
        answerable = qa.get("should_have_answer", True)
        qa_region = qa.get("region")

        item = dict(qa)
        item["gold_auto"] = True

        # --- no_answer 질문: gold를 만들지 않는다 -----------------------
        if not answerable:
            item["relevant_chunk_keys"] = []
            item["gold_status"] = "no_answer"
            # 코퍼스에 해당 지역 문서가 새로 들어왔다면 이 질문은 더 이상
            # '자료 없음'이 아니므로 평가셋을 고쳐야 한다.
            item["needs_review"] = qa_region in corpus_regions and qa_region not in COMMON_REGIONS
            output.append(item)
            report.append({
                "id": qa.get("id"),
                "question": qa.get("question"),
                "gold_status": "no_answer",
                "selected_gold": [],
                "candidates": [],
            })
            print(f"{qa.get('id')}: {qa.get('question')}")
            print("  gold = 없음 (should_have_answer=false, 정상)")
            continue

        # --- region 하드 필터 -------------------------------------------
        pool = [c for c in chunks if region_allows(c.get("region"), qa_region)]
        if not pool:
            pool = chunks

        scored = []
        for chunk in pool:
            score, details = score_chunk(qa, chunk)
            scored.append((score, chunk, details))
        scored.sort(key=lambda x: x[0], reverse=True)

        top_score = scored[0][0] if scored else 0.0
        second = scored[1][0] if len(scored) > 1 else 0.0
        gold = []

        if top_score >= MIN_SCORE:
            for score, chunk, _ in scored[:5]:
                if score >= MIN_SCORE and score >= top_score * GOLD_RATIO:
                    gold.append(f"{chunk.get('document_id')}:{chunk.get('chunk_index')}")
                if len(gold) >= MAX_GOLD:
                    break

        needs_review = (not gold) or (top_score - second) < REVIEW_MARGIN
        review_count += bool(needs_review)

        item["relevant_chunk_keys"] = gold
        item["gold_status"] = "auto" if gold else "failed"
        item["needs_review"] = needs_review
        item["gold_top_score"] = round(top_score, 4)
        output.append(item)

        report.append({
            "id": qa.get("id"),
            "question": qa.get("question"),
            "gold_status": item["gold_status"],
            "needs_review": needs_review,
            "selected_gold": gold,
            "candidates": [
                {
                    "chunk_key": f"{chunk.get('document_id')}:{chunk.get('chunk_index')}",
                    "score": round(score, 4),
                    "title": chunk.get("title"),
                    "region": chunk.get("region"),
                    "content_preview": (chunk.get("content") or "")[:180],
                    **details,
                }
                for score, chunk, details in scored[:5]
            ],
        })

        print(f"{qa.get('id')}: {qa.get('question')}")
        print(f"  gold = {gold or '없음'}{'  <= 검수필요' if needs_review else ''}")
        for c in report[-1]["candidates"][:3]:
            print(f"  {c['chunk_key']} score={c['score']:.4f} {c['title']}")

    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    answerable = [x for x in output if x.get("should_have_answer", True)]
    print("\n완료")
    print(f"QA+gold : {OUTPUT_PATH}")
    print(f"후보검토 : {REPORT_PATH}")
    print(f"총 질문        : {len(output)}")
    print(f"answerable    : {len(answerable)}  <- 이 값이 recall 분모입니다")
    print(f"no_answer     : {len(output) - len(answerable)}")
    print(f"gold 생성 성공 : {sum(bool(x['relevant_chunk_keys']) for x in answerable)}")
    print(f"수동 검수 대상 : {review_count}건 (gold_candidates.json의 needs_review)")


if __name__ == "__main__":
    main()
