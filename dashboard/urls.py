"""관리자 대시보드 URL.

3차 app/routers/admin.py 의 엔드포인트 대응:
    GET /api/admin/stats         → dashboard:stats
    GET /api/admin/top-questions → dashboard:top_questions
    GET /api/admin/region-stats  → dashboard:region_stats
    GET /api/admin/daily-trend   → dashboard:daily_trend
    GET /api/admin/documents     → dashboard:documents
"""
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="index"),
    path("stats/", views.StatsAPIView.as_view(), name="stats"),
    path("top-questions/", views.TopQuestionsAPIView.as_view(), name="top_questions"),
    path("region-stats/", views.RegionStatsAPIView.as_view(), name="region_stats"),
    path("daily-trend/", views.DailyTrendAPIView.as_view(), name="daily_trend"),
    path("documents/", views.DocumentsAPIView.as_view(), name="documents"),
]
