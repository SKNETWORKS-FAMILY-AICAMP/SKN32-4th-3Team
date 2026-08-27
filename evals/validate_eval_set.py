"""평가셋과 코퍼스의 정합성을 검사한다.

지역을 추가하거나 재인덱싱할 때마다 실행하세요.

검사 항목
  [1] no_answer 질문 무효화 - 코퍼스에 해당 지역/주제가 생기면 정답이 뒤집힌다
  [2] 지역 단서 없는 지역 특화 질문 - region을 넘기지 않으면 원리적으로 불가
  [3] 고아 질문 - answerable인데 뒷받침 문서가 0건 (평가 결과를 통째로 왜곡)
  [4] common 오염 - 지역 규칙 문서가 region 태그 없이 common에 섞였는가
  [5] 지역 커버리지 불균형
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
QA_PATH = BASE_DIR / "evals" / "qa_set.json"
CHUNK_PATH = BASE_DIR / "vector_db" / "chunks.json"

COMMON_REGIONS = {None, "", "common", "national"}

# region 코드 -> 본문/질문에서 찾을 표기들
REGION_ALIASES = {
    "seoul": ["서울"],
    "busan_namgu": ["부산", "남구"],
    "cheonan": ["천안"],
    "jeju": ["제주"],
    "sejong": ["세종"],
    "incheon_michuhol": ["인천", "미추홀"],
    "daegu": ["대구"],
    "daejeon": ["대전"],
    "ulsan": ["울산"],
    "suwon": ["수원"],
    "gwangju_bukgu": ["광주"],
}

# 지역별로 값이 갈리는 표현. common 문서에 이런 말이 있으면 태깅을 의심해야 한다.
REGIONAL_MARKERS = [
    "배출요일", "배출 요일", "배출시간", "배출 시간",
    "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
    "클린하우스", "자동크린넷", "크린넷", "자원순환역", "재활용도움센터",
    "납부필증", "티머니", "캐시비", "특수규격마대",
    "구청", "군청", "행정복지센터", "조례",
]


def aliases_for(region: str) -> list[str]:
    if region in REGION_ALIASES:
        return REGION_ALIASES[region]
    return [region.split("_")[0]]


def main() -> None:
    qa_set = json.loads(QA_PATH.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNK_PATH.read_text(encoding="utf-8"))

    corpus_regions = Counter(c.get("region") for c in chunks)
    qa_regions = Counter(q.get("region") for q in qa_set)

    real_corpus = {r for r in corpus_regions if r not in COMMON_REGIONS}
    # no_answer 전용 지역은 '문서가 없는 것이 정상'이므로 커버리지 경고에서 제외
    _answerable_regions = {
        q.get("region") for q in qa_set if q.get("should_have_answer", True)
    }
    # answerable 문항이 하나도 없는 지역만 'no_answer 전용'이다.
    no_answer_regions = ({
        q.get("region") for q in qa_set if not q.get("should_have_answer", True)
    } - COMMON_REGIONS) - _answerable_regions
    answerable_qa_regions = {
        q.get("region") for q in qa_set if q.get("should_have_answer", True)
    } - COMMON_REGIONS

    problems = 0

    print("=" * 80)
    print("코퍼스 지역 분포")
    print("=" * 80)
    total = len(chunks)
    for region, n in corpus_regions.most_common():
        n_q = qa_regions.get(region, 0)
        share = n / total * 100 if total else 0
        print(f"  {str(region):<22} chunks={n:<6}({share:4.1f}%)  questions={n_q}")

    common_n = sum(corpus_regions[r] for r in corpus_regions if r in COMMON_REGIONS)
    if total and common_n / total > 0.6:
        print()
        print(f"  ! common 비중이 {common_n / total * 100:.0f}%입니다.")
        print("    지자체별 규칙 코퍼스에서 이 비중이면 region 태깅 누락을 의심하세요.")
        print("    region 필터를 켜도 후보가 거의 줄지 않아 필터가 무력해집니다.")

    # ---------------------------------------------------------------- [1]
    print()
    print("=" * 80)
    print("[1] no_answer 질문 무효화 검사")
    print("=" * 80)
    found = 0
    for qa in qa_set:
        if qa.get("should_have_answer", True):
            continue
        text = qa.get("question", "")
        hit = [r for r in real_corpus if any(a in text for a in aliases_for(r))]
        if hit:
            found += 1
            problems += 1
            print(f"  [무효] {qa['id']}: {text}")
            print(f"         코퍼스에 {hit} 문서가 존재 -> '자료 없음'이 오답이 됨")
    if not found:
        print("  이상 없음")

    # ---------------------------------------------------------------- [2]
    print()
    print("=" * 80)
    print("[2] 지역 단서 없는 지역 특화 질문")
    print("=" * 80)
    blind = 0
    intended = 0
    for qa in qa_set:
        region = qa.get("region")
        if region in COMMON_REGIONS or not qa.get("should_have_answer", True):
            continue
        # 지역별로 답이 갈리는 문항만 대상. 품목 판정(single_fact)이나
        # 정책 문항(cross_reference)은 질문에 지역명이 없는 것이 정상이다.
        if qa.get("type") not in ("region_specific", "trap", "region_rule"):
            continue
        # 작성자가 명시적으로 대조군으로 만든 문항은 의도된 것이다.
        if qa.get("region_explicit") is False:
            intended += 1
            continue
        text = qa.get("question", "")
        if any(a in text for a in aliases_for(region)):
            continue
        blind += 1
        print(f"  [무단서] {qa['id']} (region={region}): {text}")
    if intended:
        print(f"  (region_explicit=false 로 표시된 의도된 대조군 {intended}건은 제외)")
    if blind:
        print()
        print(f"  총 {blind}건. region을 검색기에 넘기지 않으면 원리적으로 불가합니다.")
        print("  단, common 비중이 높으면 region 필터를 붙여도 효과가 제한적입니다.")
    else:
        print("  이상 없음")

    # ---------------------------------------------------------------- [3]
    print()
    print("=" * 80)
    print("[3] 고아 질문 - answerable인데 뒷받침 문서 0건  <<< 최우선")
    print("=" * 80)
    orphan_by_region: dict[str, list[str]] = defaultdict(list)
    for qa in qa_set:
        if not qa.get("should_have_answer", True):
            continue
        region = qa.get("region")
        if region in COMMON_REGIONS:
            continue
        if corpus_regions.get(region, 0) == 0:
            orphan_by_region[region].append(qa["id"])

    orphan_total = sum(len(v) for v in orphan_by_region.values())
    answerable_total = sum(q.get("should_have_answer", True) for q in qa_set)
    if orphan_total:
        problems += orphan_total
        for region, ids in sorted(orphan_by_region.items()):
            print(f"  [고아] {region:<18} {len(ids):>2}건  {', '.join(ids)}")
        print()
        print(f"  총 {orphan_total}건 / answerable {answerable_total}건 "
              f"= {orphan_total / answerable_total * 100:.0f}%")
        print("  이 문항들은 어떤 검색기로도 맞힐 수 없어 결과를 통째로 왜곡합니다.")
        print("  해당 지역 문서를 인덱싱하거나, 평가에서 제외한 뒤 실행하세요.")
        print()
        print("  임시 제외 명령:")
        print("    python evals/validate_eval_set.py --split-orphans")
    else:
        print("  이상 없음")

    # ---------------------------------------------------------------- [4]
    print()
    print("=" * 80)
    print("[4] common 오염 - 지역 규칙이 태그 없이 common에 섞였는가")
    print("=" * 80)
    suspects = []
    for c in chunks:
        if c.get("region") not in COMMON_REGIONS:
            continue
        content = c.get("content") or ""
        named = [r for r in REGION_ALIASES if any(a in content for a in REGION_ALIASES[r])]
        markers = [m for m in REGIONAL_MARKERS if m in content]
        if named and markers:
            suspects.append((c, named, markers))

    if suspects:
        print(f"  common 문서 {len(suspects)}건에서 지역명 + 지역별 표현이 함께 발견됐습니다.")
        print("  (전국 공통 문서라면 특정 지역명과 요일·수거방식이 같이 나올 이유가 없습니다)")
        print()
        for c, named, markers in suspects[:15]:
            key = f"{c.get('document_id')}:{c.get('chunk_index')}"
            print(f"  {key:<24} 지역={named}  표현={markers[:4]}")
            print(f"    {(c.get('content') or '')[:90]}...")
        if len(suspects) > 15:
            print(f"  ... 외 {len(suspects) - 15}건")
        print()
        print("  -> 이 문서들에 region 태그를 부여하면 필터가 비로소 동작합니다.")
    else:
        print("  이상 없음")

    # ---------------------------------------------------------------- [5]
    print()
    print("=" * 80)
    print("[5] 지역 커버리지 불균형")
    print("=" * 80)
    no_question = sorted(real_corpus - answerable_qa_regions)
    if no_question:
        print(f"  문서만 있고 질문 없음 : {no_question}")
        print("    -> 지역당 최소 4~5문항은 있어야 지역별 성능을 쪼개 볼 수 있습니다.")
    if no_answer_regions:
        print(f"  no_answer 전용 지역   : {sorted(no_answer_regions)} (문서 없음이 정상)")

    thin = [r for r in real_corpus if corpus_regions[r] < 10]
    if thin:
        print(f"  청크 10개 미만 지역   : {sorted(thin)}")
        print("    -> 문서가 얇으면 그 지역 질문의 R@5 상한이 문서 수에 갇힙니다.")
    if not no_question and not thin:
        print("  이상 없음")

    print()
    print(f"질문 총 {len(qa_set)}건 / 청크 총 {len(chunks)}건 / 발견된 문제 {problems}건")
    if problems:
        print("\n문제가 남아 있는 동안에는 run_retrieval_eval.py 결과를 신뢰하지 마세요.")


def split_orphans() -> None:
    """고아 질문을 qa_set_pending.json으로 분리하고 나머지만 qa_set_active.json에 남긴다.

    문서를 넣기 전 임시로 평가를 돌리고 싶을 때 사용한다.
    문서를 인덱싱한 뒤에는 pending을 qa_set.json에 다시 합칠 것.
    """
    qa_set = json.loads(QA_PATH.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNK_PATH.read_text(encoding="utf-8"))
    corpus_regions = Counter(c.get("region") for c in chunks)

    active, pending = [], []
    for qa in qa_set:
        region = qa.get("region")
        orphan = (
            qa.get("should_have_answer", True)
            and region not in COMMON_REGIONS
            and corpus_regions.get(region, 0) == 0
        )
        (pending if orphan else active).append(qa)

    (BASE_DIR / "evals" / "qa_set_active.json").write_text(
        json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")
    (BASE_DIR / "evals" / "qa_set_pending.json").write_text(
        json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"active  {len(active)}건 -> evals/qa_set_active.json")
    print(f"pending {len(pending)}건 -> evals/qa_set_pending.json")
    print()
    print("build_gold_chunks.py의 QA_PATH를 qa_set_active.json으로 바꿔 실행하세요.")
    print("문서를 인덱싱한 뒤에는 pending을 qa_set.json에 다시 합칠 것.")


if __name__ == "__main__":
    import sys
    if "--split-orphans" in sys.argv:
        split_orphans()
    else:
        main()