"""[RAG 파트] 유사도 임계값(RAG_MIN_SCORE)을 정하기 위한 측정 도구.

    python manage.py measure_threshold

3차 scripts/measure_threshold.py 를 그대로 옮긴 것입니다 (측정 방법이
달라지면 3차 지표와의 비교가 무효가 되므로 본문 무수정 — import 교체와
BaseCommand 래핑만 했습니다).

무엇을 하나
  "우리 문서에 답이 있는 질문"과 "없는 질문"을 각각 검색해서
  최고 유사도 점수 분포를 비교한다. 두 그룹이 갈리는 지점이 임계값 후보다.

**지역별로 따로 측정한다.**
  실제 챗봇은 사용자가 지역을 고른 상태로 검색하므로,
  해당 지역 + 공통(common) 문서만 후보가 된다.
  지역을 지정하지 않고 측정하면 다른 지역 문서까지 후보에 들어가
  점수가 실제보다 높게 나오고, 임계값이 느슨하게 잡힌다.

  (예: "종이컵" 질문은 서울 0.372 / 천안 0.336 / 부산 0.267 로
   지역마다 최고점이 다르다. 전체 기준으로 0.36 을 잡으면
   천안·부산 사용자는 답을 못 받는다.)

출력된 점수 분포는 그대로 테스트 보고서의 임계값 근거 자료가 된다.

주의(HANDOFF 4번): hash 임베딩으로는 측정하지 마십시오 — 무관한 질문도
2-gram 우연 일치로 점수가 잡혀 분포가 의미를 잃습니다.
"""
from __future__ import annotations

import json
from datetime import datetime

from django.conf import settings  # 3차: from app.core.config import settings
from django.core.management.base import BaseCommand

from rag import service as rag_service  # 3차: from app.services import rag_service

# 실제 서비스에서 사용자가 고를 수 있는 지역
REGIONS = ["seoul", "cheonan", "busan_namgu", "sejong", "incheon_michuhol", "jeju"]

# ── 우리 문서에 답이 있어야 하는 질문 ──
RELEVANT = [
    "종이컵은 어떻게 버리나요?",
    "달걀 껍질은 음식물쓰레기인가요?",
    "부탄가스 통은 어떻게 배출하나요?",
    "투명 페트병 라벨을 떼야 하나요?",
    "깨진 유리는 어디에 버려요?",
    "헌 옷은 어떻게 처리하나요?",
    "냉장고는 어떻게 버리나요?",
    "먹다 남은 약은 어디에 버려요?",
]

# ── 우리 문서에 답이 없는 질문 (지역·품목 미수집 / 무관한 주제) ──
IRRELEVANT = [
    "제주도에서 페트병은 어떻게 버리나요?",
    "대구 수성구 음식물쓰레기 배출 요일은?",
    "전기차 폐배터리는 어디에 신고하나요?",
    "주식 투자는 어떻게 시작하나요?",
    "오늘 서울 날씨 알려줘",
    "회사 연차는 며칠인가요?",
]


def top_score(question: str, region: str | None) -> float:
    """임계값을 끄고 검색해 최고 점수를 돌려준다."""
    results = rag_service.search(
        question, top_k=1, owner_id=None, min_score=0.0, region=region
    )
    return results[0]["score"] if results else 0.0


def measure(questions: list[str], region: str | None) -> list[float]:
    return [top_score(q, region) for q in questions]


def print_table(title: str, questions: list[str], scores_by_region: dict) -> None:
    print(f"\n── {title} ──")
    header = "".join(f"{r:>14}" for r in scores_by_region)
    print(f"  {'질문':<30}{header}")
    for i, q in enumerate(questions):
        row = "".join(f"{scores_by_region[r][i]:>14.4f}" for r in scores_by_region)
        print(f"  {q[:28]:<30}{row}")


def suggest(relevant: list[float], irrelevant: list[float], label: str) -> float | None:
    low = min(relevant)
    high = max(irrelevant)
    print(f"\n[{label}] 관련 있음 최저 {low:.4f} / 관련 없음 최고 {high:.4f}")

    if low > high:
        candidate = round((low + high) / 2, 2)
        print(f"   두 그룹이 분리됨 → 권장 {candidate}")
        return candidate

    print(f"   겹침 구간 {high:.4f} ~ {low:.4f}")
    return None


def main() -> None:
    print(f"임베딩 백엔드: {settings.EMBEDDING_BACKEND}")
    print(f"현재 임계값  : {settings.RAG_MIN_SCORE}")

    if settings.EMBEDDING_BACKEND.lower() == "local":
        print("\n[경고] local 임베딩은 점수 체계가 다릅니다.")
        print("       실제 임계값은 EMBEDDING_BACKEND=gemini·openai 에서 측정하세요.")

    rel: dict[str, list[float]] = {}
    irr: dict[str, list[float]] = {}

    for region in REGIONS:
        rel[region] = measure(RELEVANT, region)
        irr[region] = measure(IRRELEVANT, region)

    print_table("답이 있어야 하는 질문", RELEVANT, rel)
    print_table("답이 없어야 하는 질문", IRRELEVANT, irr)

    # 지역별 권장값
    print("\n" + "─" * 58)
    per_region = {r: suggest(rel[r], irr[r], r) for r in REGIONS}

    # 모든 지역에서 통하는 값
    #   가장 낮은 "관련 있음" 을 살려야 어느 지역 사용자도 답을 받는다.
    worst_relevant = min(min(v) for v in rel.values())
    worst_irrelevant = max(max(v) for v in irr.values())

    print("\n" + "═" * 58)
    print("전체 지역 공통 기준")
    print(f"  관련 있음 최저 (가장 불리한 지역) : {worst_relevant:.4f}")
    print(f"  관련 없음 최고 (가장 불리한 지역) : {worst_irrelevant:.4f}")

    if worst_relevant > worst_irrelevant:
        common = round((worst_relevant + worst_irrelevant) / 2, 2)
        print(f"\n  권장 임계값: {common}")
        print(f"  RAG_MIN_SCORE={common}")
    else:
        print("\n  [주의] 모든 지역을 만족하는 값이 없습니다.")
        print(f"  · 답변율 우선 → {max(0.0, round(worst_relevant - 0.02, 2))} 이하")
        print("    (무관한 근거가 일부 통과하므로 프롬프트 방어가 필요)")
        print("  · 환각 방지 우선 → 높게 잡되, 일부 지역에서 답을 못 받는 질문이 생김")
        print("  · 근본 해결: 해당 지역 가이드를 보강해 최저 점수를 끌어올릴 것")

    # 어느 지역에서 답을 못 받는지 짚어준다
    threshold = settings.RAG_MIN_SCORE
    print(f"\n현재 임계값 {threshold} 기준 '답을 못 받는' 질문")
    found = False
    for region in REGIONS:
        misses = [
            f"{RELEVANT[i][:24]} ({s:.3f})"
            for i, s in enumerate(rel[region])
            if s < threshold
        ]
        if misses:
            found = True
            print(f"  [{region}] {len(misses)}건")
            for m in misses:
                print(f"     - {m}")
    if not found:
        print("  없음")

    path = settings.INDEX_DIR.parent / f"threshold_{datetime.now():%Y%m%d_%H%M}.json"
    path.write_text(
        json.dumps(
            {
                "measured_at": datetime.now().isoformat(timespec="seconds"),
                "embedding_backend": settings.EMBEDDING_BACKEND,
                "current_threshold": threshold,
                "by_region": {
                    r: {
                        "relevant": dict(zip(RELEVANT, rel[r])),
                        "irrelevant": dict(zip(IRRELEVANT, irr[r])),
                        "suggested": per_region[r],
                    }
                    for r in REGIONS
                },
                "worst_relevant": worst_relevant,
                "worst_irrelevant": worst_irrelevant,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n측정 결과 저장: {path}")


class Command(BaseCommand):
    help = "정답셋으로 유사도 분포를 측정해 RAG_MIN_SCORE 권장값을 출력합니다."

    def handle(self, *args, **options):
        main()
