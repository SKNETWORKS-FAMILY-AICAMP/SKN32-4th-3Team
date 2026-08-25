"""관리자 통계 View.

3차 app/routers/admin.py(250줄)의 SQLAlchemy 집계를 Django ORM 으로
재작성한 것입니다. **응답 JSON 의 키·형식은 3차와 동일하게 유지**해서
static/app.js 의 대시보드 렌더링 코드를 fetch 경로만 바꿔 재사용할 수
있게 했습니다.

■ 3차 → 4차 대응
    GET /api/admin/stats          → dashboard:stats
    GET /api/admin/top-questions  → dashboard:top_questions
    GET /api/admin/region-stats   → dashboard:region_stats
    GET /api/admin/daily-trend    → dashboard:daily_trend
    GET /api/admin/documents      → dashboard:documents
    POST /api/admin/upload        → rag:upload (사용자 기능으로 승격,
                                    색인 누락 버그 수정 — rag/views.py 참고)

■ 집계 재작성 대응표
    sql_func.count(ChatLog.id)                 → ChatLog.objects.count()
    filter(ChatLog.created_at >= today)        → .filter(created_at__gte=today)
    count(distinct(ChatLog.user_id))           → .filter(user__isnull=False)
                                                  .values("user").distinct().count()
    group_by(question) + count 정렬            → .values("question")
                                                  .annotate(cnt=Count("id"))
                                                  .order_by("-cnt")

■ 3차와 의도적으로 다르게 한 곳
    1. 시간 기준: 3차는 datetime.now()(naive) 였습니다. settings 에
       USE_TZ=True, TIME_ZONE="Asia/Seoul" 이므로 timezone.localtime()
       으로 aware 하게 계산합니다. naive 로 두면 DB 의 UTC 저장값과
       비교할 때 "오늘"의 경계가 9시간 어긋납니다.
    2. 활성 사용자: SQL 의 COUNT(DISTINCT user_id) 는 NULL 을 무시하지만
       Django 의 .values("user").distinct() 는 NULL 을 한 그룹으로
       셉니다. 3차 의미를 유지하려고 user__isnull=False 를 명시했습니다.
    3. daily-trend: 3차는 7일을 루프 돌며 쿼리 7번을 날렸습니다.
       TruncDate 집계 한 번으로 바꾸고 빈 날짜를 파이썬에서 채웁니다.
       (응답 형식은 동일)
    4. 지역 라벨: 3차 admin.py 에는 REGION_LABELS 사전이 두 벌(전체본과
       documents 용 축약본) 중복돼 있었고 members 쪽 코드와도 따로
       놀았습니다. members.models.REGION_CHOICES 한 곳에서 파생합니다.
"""
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from chat.models import ChatLog
from members.models import REGION_CHOICES
from rag.models import QuestionCluster

# 지역 코드 → 표시 이름. 3차의 REGION_LABELS 사전 두 벌을 대체합니다.
REGION_LABELS = dict(REGION_CHOICES)

# 3차 daily-trend 의 요일 표기를 그대로 유지합니다.
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def _json(payload, status=200):
    """한글이 \\uXXXX 로 이스케이프되지 않게 하는 공통 응답 헬퍼."""
    return JsonResponse(
        payload, status=status, safe=False, json_dumps_params={"ensure_ascii": False}
    )


def _today_start():
    """서울 기준 오늘 0시(aware). 모든 기간 집계의 기준점입니다."""
    now = timezone.localtime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """서비스 관리자만 접근을 허용합니다.

    3차의 `"admin" in user.email.lower()` 판정을 is_staff 기반으로
    바꿨습니다 (badmin@example.com 오인 문제 — members/models.py 참고).
    권한이 없으면 UserPassesTestMixin 이 403 을 반환합니다.
    """

    def test_func(self):
        return self.request.user.is_service_admin


class DashboardView(AdminRequiredMixin, View):
    """대시보드 화면. 숫자는 아래 JSON 엔드포인트들이 채웁니다.

    3차 static/app.js 의 admin-page 렌더링 방식(페이지 로드 후 fetch)을
    그대로 쓰기 위해 화면은 빈 껍데기만 서버 렌더링합니다.
    """

    def get(self, request):
        # 4차 2R 추가분: 관리사무소 관리자 승인 대기 건수. 링크 배지 하나만
        # 추가하고, apartments 앱 자체의 큐 화면은 apartments/views.py 에
        # 그대로 둔다 — AdminRequiredMixin(is_staff 전역 권한)과 단지
        # 스코프 권한을 섞지 않기 위해서다.
        from apartments.models import Membership

        pending_apartment_managers = Membership.objects.filter(
            role=Membership.Role.MANAGER, status=Membership.Status.REQUESTED,
        ).count()
        return render(
            request, "dashboard/index.html",
            {"pending_apartment_managers": pending_apartment_managers},
        )


class StatsAPIView(AdminRequiredMixin, View):
    """요약 통계. 3차 GET /api/admin/stats 와 응답 키가 동일합니다.

    반환: {total, today, yesterday, today_diff,
           active_users, success_rate, week_change}
    """

    def get(self, request):
        today = _today_start()
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)
        prev_week = week_ago - timedelta(days=7)

        logs = ChatLog.objects

        total = logs.count()
        today_count = logs.filter(created_at__gte=today).count()
        yesterday_count = logs.filter(
            created_at__gte=yesterday, created_at__lt=today
        ).count()

        # 이번 주 활성 사용자.
        # COUNT(DISTINCT user_id) 는 NULL 무시 — Django 에서 같은 의미가
        # 되도록 user__isnull=False 를 명시합니다 (상단 주석 2번).
        active_users = (
            logs.filter(created_at__gte=week_ago, user__isnull=False)
            .values("user")
            .distinct()
            .count()
        )

        # 답변 성공률 (근거 기반 답변 비율)
        answered = logs.filter(has_answer=True).count()
        success_rate = round(answered / total * 100) if total > 0 else 0

        # 지난주 대비 증감
        this_week = logs.filter(created_at__gte=week_ago).count()
        last_week = logs.filter(
            created_at__gte=prev_week, created_at__lt=week_ago
        ).count()
        week_change = (
            round((this_week - last_week) / last_week * 100) if last_week > 0 else 0
        )

        return _json(
            {
                "total": total,
                "today": today_count,
                "yesterday": yesterday_count,
                "today_diff": today_count - yesterday_count,
                "active_users": active_users,
                "success_rate": success_rate,
                "week_change": week_change,
            }
        )


class TopQuestionsAPIView(AdminRequiredMixin, View):
    """인기 질문 TOP N. 3차 GET /api/admin/top-questions 대응.

    임베딩 클러스터 기반이 우선이고, 클러스터가 하나도 없으면 3차와
    동일하게 문자열 완전 일치 GROUP BY 로 폴백합니다.
    (클러스터 방식의 도입 배경은 rag/models.py QuestionCluster 참고 —
     "종이컵 버리는 법"과 "종이컵은 어떻게 버려요?"를 하나로 묶기 위함)
    """

    def get(self, request):
        limit = self._limit(request)

        clusters = QuestionCluster.objects.order_by("-count")[:limit]
        if clusters:
            return _json(
                [{"question": c.representative, "count": c.count} for c in clusters]
            )

        # 클러스터가 없을 때의 폴백 (3차와 동일한 GROUP BY 방식)
        rows = (
            ChatLog.objects.values("question")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")[:limit]
        )
        return _json([{"question": r["question"], "count": r["cnt"]} for r in rows])

    @staticmethod
    def _limit(request, default=5, maximum=20):
        try:
            return max(1, min(int(request.GET.get("limit", default)), maximum))
        except ValueError:
            return default


class RegionStatsAPIView(AdminRequiredMixin, View):
    """지역별 질문 분포. 3차 GET /api/admin/region-stats 대응.

    반환: [{"region", "label", "count"}, ...] (질문 수 내림차순)
    """

    def get(self, request):
        rows = (
            ChatLog.objects.values("region")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
        )
        return _json(
            [
                {
                    "region": r["region"],
                    "label": REGION_LABELS.get(r["region"], r["region"]),
                    "count": r["cnt"],
                }
                for r in rows
            ]
        )


class DailyTrendAPIView(AdminRequiredMixin, View):
    """최근 N일 일별 질문 수. 3차 GET /api/admin/daily-trend 대응.

    3차는 날짜마다 쿼리를 한 번씩(기본 7번) 날렸습니다. TruncDate 집계
    한 번으로 바꾸고, 질문이 없던 날짜는 파이썬에서 0 으로 채웁니다.
    응답 형식은 3차와 동일합니다: [{"date": "08/24", "day": "일", "count": 3}, ...]
    """

    def get(self, request):
        try:
            days = max(1, min(int(request.GET.get("days", 7)), 31))
        except ValueError:
            days = 7

        today = _today_start()
        start = today - timedelta(days=days - 1)

        # TruncDate 는 TIME_ZONE 기준으로 날짜를 자릅니다 (USE_TZ=True).
        rows = (
            ChatLog.objects.filter(created_at__gte=start)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(cnt=Count("id"))
        )
        counts = {r["day"]: r["cnt"] for r in rows}

        result = []
        for i in range(days):
            d = (start + timedelta(days=i)).date()
            result.append(
                {
                    "date": d.strftime("%m/%d"),
                    "day": WEEKDAY_KO[d.weekday()],
                    "count": counts.get(d, 0),
                }
            )
        return _json(result)


class DocumentsAPIView(AdminRequiredMixin, View):
    """색인된 문서 목록과 청크 수. 3차 GET /api/admin/documents 대응.

    DB 가 아니라 **chunks.json(색인 메타)** 을 읽습니다. 3차와 같은
    선택인데 이유가 있습니다 — 대시보드의 관심사는 "지금 검색에 실제로
    잡히는 문서"이지 "DB 에 저장된 문서"가 아닙니다. 문서를 올리고
    재색인을 안 했다면 그 차이가 이 화면에서 드러나야 합니다.

    반환: {index_exists, total_chunks,
           documents: [{title, source_type, region,
                        chunk_count, region_label, type_label}, ...]}
    """

    TYPE_LABELS = {"guide": "가이드", "law": "법령", "manual": "사용자 문서"}

    def get(self, request):
        import json

        from django.conf import settings

        meta_path = settings.INDEX_DIR / "chunks.json"
        if not meta_path.exists():
            return _json({"index_exists": False, "documents": [], "total_chunks": 0})

        chunks = json.loads(meta_path.read_text(encoding="utf-8"))

        # 문서별 청크 수 집계 (제목 정리는 rag.service 의 것을 재사용 —
        # 3차 admin.py 는 같은 정규식을 자리에서 다시 만들었습니다)
        from rag.service import _clean_title

        doc_map: dict[str, dict] = {}
        for chunk in chunks:
            title = chunk.get("title", "제목 없음")
            if title not in doc_map:
                doc_map[title] = {
                    "title": _clean_title(title),
                    "source_type": chunk.get("source_type", "manual"),
                    "region": chunk.get("region") or "common",
                    "chunk_count": 0,
                }
            doc_map[title]["chunk_count"] += 1

        docs = list(doc_map.values())
        for d in docs:
            d["region_label"] = REGION_LABELS.get(d["region"], d["region"])
            d["type_label"] = self.TYPE_LABELS.get(d["source_type"], "기타")

        return _json(
            {"index_exists": True, "documents": docs, "total_chunks": len(chunks)}
        )
